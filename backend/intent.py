"""Step 1: understand the request -> structured intent.

Two paths that produce the *same* schema:
  * Gemini (if GEMINI_API_KEY is set): robust to slang, any language, typos.
  * Rules (always available): bilingual EN/ZH lexicon. Also the fallback if the API call fails,
    and it fills any field the LLM left empty.

Intent schema
{
  "category":  food_assistance | donation | learning | study_space | None,
  "need_type": donate | receive | learn | space | None,
  "needs":     canonical tags, e.g. ["furniture"] or ["math"] or ["halal"],
  "constraints": {free, open_now, no_id, no_appointment, near_campus},
  "identity":  subset of [students, newcomers, low_income, seniors],
  "campus":    uw | wlu | conestoga | None,
  "urgent":    bool,
  "language":  "zh" | "en",
  "query_text": English text used for the semantic index,
  "source":    "llm" | "rules"
}
"""
from __future__ import annotations
import json
import re

from . import config

CATEGORIES = ["food_assistance", "donation", "learning", "study_space"]
NEED_TYPES = ["donate", "receive", "learn", "space"]
IDENTITIES = ["students", "newcomers", "low_income", "seniors"]  # same tags as resource.eligibility
CONSTRAINTS = ["free", "open_now", "open_24h", "no_id", "no_appointment", "near_campus"]
CAMPUSES = ["uw", "wlu", "conestoga"]

# canonical need tag -> (trigger words EN+ZH, English expansion for semantic search)
NEEDS = {
    # donation items
    "furniture": (["furniture", "sofa", "couch", "desk", "dresser", "bookshelf", "wardrobe", "chair", "家具", "沙发", "桌子", "椅子", "书桌", "书架", "柜子"], "furniture sofa couch table chair dresser"),
    "electronics": (["laptop", "computer", "electronics", "tablet", "ipad", "monitor", "phone", "笔记本", "电脑", "电子", "平板", "显示器", "手机"], "laptop computer tablet electronics"),
    "clothing": (["clothes", "clothing", "jacket", "coat", "coats", "sweater", "衣服", "外套", "羽绒服", "冬衣", "衣物"], "clothing coats winter clothes"),
    "books": (["book", "books", "textbook", "textbooks", "novel", "书", "教材", "课本"], "books textbooks"),
    "kitchenware": (["kitchenware", "dishes", "cookware", "pots", "pans", "microwave", "appliance", "厨具", "锅", "餐具", "微波炉"], "kitchenware dishes cookware"),
    "bedding": (["bedding", "blanket", "blankets", "pillow", "sheets", "被子", "床单", "枕头", "毯子"], "bedding blankets"),
    "bikes": (["bike", "bikes", "bicycle", "自行车", "单车"], "bicycle bike"),
    "toys": (["toy", "toys", "board game", "玩具", "桌游"], "toys games children"),
    "food": (["canned", "non-perishable", "food donation", "罐头", "捐食物", "捐粮"], "non-perishable food canned goods"),
    "mattress": (["mattress", "床垫"], "mattress bed"),
    # services
    "math": (["math", "calculus", "algebra", "statistics", "数学", "微积分", "线性代数", "统计"], "math calculus tutoring"),
    "english": (["english", "esl", "ielts", "toefl", "speaking", "conversation practice", "英语", "英文", "口语"], "english conversation esl"),
    "writing": (["essay", "writing", "paper", "thesis", "写作", "论文", "作文"], "writing essay help"),
    "coding": (["coding", "programming", "python", "javascript", "code", "编程", "代码", "写代码"], "coding programming python club"),
    "resume": (["resume", "cv", "cover letter", "interview", "简历", "求职", "面试"], "resume career interview help"),
    "science": (["physics", "chemistry", "biology", "science", "物理", "化学", "生物", "科学"], "science physics chemistry"),
    "homework": (["homework", "assignment", "作业"], "homework help"),
    "computer_skills": (["computer skills", "basic computer", "digital skills", "digital literacy", "tech tutor", "电脑技能", "电脑使用", "数字技能"], "computer skills digital literacy technology help"),
    "hot_meals": (["hot meal", "hot meals", "hot lunch", "hot dinner", "dinner", "lunch", "热饭", "热餐", "午饭", "晚饭", "吃一顿热"], "hot meal lunch dinner served"),
    "halal": (["halal", "清真"], "halal"),
    "baby_supplies": (["baby", "formula", "diaper", "diapers", "infant", "婴儿", "奶粉", "尿布", "宝宝"], "baby formula diapers"),
    "produce": (["fresh produce", "vegetables", "fruit", "新鲜蔬菜", "水果", "蔬菜"], "fresh produce vegetables fruit"),
    "groceries": (["groceries", "grocery", "pantry", "食品库", "买菜", "杂货"], "groceries pantry food"),
    "quiet_study": (["quiet", "silent", "安静"], "quiet silent study"),
    "group_rooms": (["group study", "group room", "meeting room", "小组讨论", "讨论室"], "group study rooms"),
    "wifi": (["wifi", "wi-fi", "internet", "无线网", "网络"], "wifi internet"),
}
DONATION_ITEMS = {"furniture", "electronics", "clothing", "books", "kitchenware", "bedding", "bikes", "toys", "food", "mattress"}

CATEGORY_WORDS = {
    "food_assistance": (["food", "hungry", "meal", "groceries", "pantry", "food bank", "食物", "吃的", "饿", "食品", "粮食", "食品银行", "没饭吃", "吃饭"], "food assistance pantry groceries meals"),
    "learning": (["tutor", "tutoring", "homework", "lesson", "class", "learn", "workshop", "mentor", "辅导", "补习", "学习小组", "上课", "答疑", "家教", "学英语", "培训"], "free tutoring learning help"),
    "study_space": (["study", "library", "自习", "图书馆", "看书", "复习", "study space"], "quiet place to study library"),
    "donation": (["donate", "donation", "give away", "drop off", "get rid of", "dispose", "捐", "捐赠", "送人", "不要了", "处理掉", "闲置"], "donate accept drop-off donation"),
}
DONATE_VERBS = ["donate", "donation", "give away", "give it away", "drop off", "get rid of", "don't need", "don’t need", "no longer need",
                "捐", "送人", "不要了", "处理", "闲置", "转让", "送给"]
SPACE_HINTS = ["quiet", "place to study", "study space", "study spot", "study room", "安静", "自习", "看书", "复习", "地方学习", "学习的地方"]
LEARN_HINTS = ["tutor", "tutoring", "homework", "help with", "esl", "class", "club", "lesson", "辅导", "补习", "答疑", "口语", "俱乐部", "教", "简历", "练习"]
FOOD_HINTS = ["food", "hungry", "meal", "groceries", "pantry", "食物", "吃的", "饿", "食品", "粮", "吃饭", "热饭", "热餐", "清真", "奶粉", "尿布"]

CONSTRAINT_WORDS = {
    "free": ["free", "no cost", "for free", "免费", "不要钱", "不收费", "没钱"],
    "open_now": ["open now", "right now", "open today", "现在营业", "现在开", "现在能去", "马上", "现在就能"],
    "open_24h": ["24 hour", "24-hour", "24/7", "24h", "all night", "round the clock", "通宵", "24小时", "全天开放", "全天候"],
    "no_id": ["no id", "without id", "don't have any id", "dont have any id", "don't have id", "do not have id", "no identification", "without any id", "no documents", "don't have id", "no papers", "不需要证件", "没有证件", "没身份证", "无需证件", "不用证件"],
    "no_appointment": ["walk-in", "walk in", "no appointment", "without appointment", "drop-in", "drop in", "不用预约", "无需预约", "直接去", "不需要预约"],
    "near_campus": ["near campus", "on campus", "campus", "校园", "学校附近", "校内", "学校旁边"],
}
IDENTITY_WORDS = {
    "students": ["student", "university", "college", "freshman", "学生", "大一", "大学", "留学生", "研究生"],
    "newcomers": ["newcomer", "just moved", "just arrived", "new to canada", "immigrant", "refugee", "international student", "新移民", "刚来", "刚搬来", "新来", "留学生", "移民", "难民"],
    "low_income": ["low income", "low-income", "can't afford", "cannot afford", "tight budget", "broke", "没钱", "经济困难", "低收入", "负担不起", "穷"],
    "seniors": ["senior", "elderly", "老人", "老年"],
}
CAMPUS_WORDS = {
    "uw": ["university of waterloo", "uwaterloo", " uw ", "滑铁卢大学", "uw校园"],
    "wlu": ["laurier", "wlu", "劳里埃"],
    "conestoga": ["conestoga", "康尼斯托加"],
}
URGENT_WORDS = ["homeless", "nowhere to sleep", "no place to sleep", "crisis", "suicid", "kill myself", "abuse", "unsafe at home",
                "emergency", "starving", "无家可归", "没地方住", "没地方睡", "危机", "自杀", "不想活", "家暴", "紧急", "被打"]


def _has(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def _word_in(text: str, w: str) -> bool:
    """Substring for CJK, word-boundary for ASCII (so 'cv' doesn't match inside other words)."""
    if re.search(r"[\u4e00-\u9fff]", w):
        return w in text
    return re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text) is not None


def _any_word(text: str, words: list[str]) -> bool:
    return any(_word_in(text, w) for w in words)


def detect_language(text: str) -> str:
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"


def build_query_text(text: str, category: str | None, needs: list[str], lang: str) -> str:
    parts: list[str] = []
    if category:
        parts.append(CATEGORY_WORDS[category][1])
    parts += [NEEDS[n][1] for n in needs if n in NEEDS]
    if lang == "en":
        parts.append(text)
    return " ".join(parts).strip()


def parse_intent_rules(text: str) -> dict:
    t = " " + text.lower().strip() + " "
    lang = detect_language(text)

    needs = [k for k, (words, _) in NEEDS.items() if _any_word(t, words)]
    # 'coat' etc. already specific; drop generic overlaps
    if "groceries" in needs and any(n in needs for n in ("halal", "baby_supplies", "produce")):
        needs.remove("groceries")
    if "homework" in needs and "math" in needs:
        needs.remove("homework")

    donating = _any_word(t, DONATE_VERBS)
    has_item = any(n in DONATION_ITEMS for n in needs)

    if donating or (has_item and not _any_word(t, FOOD_HINTS) and not _any_word(t, LEARN_HINTS)
                    and _any_word(t, ["have", "got", "old", "used", "有", "旧", "二手"])):
        category, need_type = "donation", "donate"
    elif _any_word(t, SPACE_HINTS) or (_any_word(t, ["study", "library"]) and not _any_word(t, LEARN_HINTS)):
        category, need_type = "study_space", "space"
        needs = [n for n in needs if n in ("quiet_study", "group_rooms", "wifi")] or (["quiet_study"] if _any_word(t, ["quiet", "安静", "silent"]) else [])
    elif _any_word(t, LEARN_HINTS) or any(n in needs for n in ("math", "english", "writing", "coding", "resume", "science", "homework")):
        category, need_type = "learning", "learn"
        needs = [n for n in needs if n in ("math", "english", "writing", "coding", "resume", "science", "homework")]
    elif _any_word(t, FOOD_HINTS) or any(n in needs for n in ("hot_meals", "halal", "baby_supplies", "produce", "groceries")):
        category, need_type = "food_assistance", "receive"
        needs = [n for n in needs if n in ("hot_meals", "halal", "baby_supplies", "produce", "groceries")]
    else:
        category, need_type, needs = None, None, []

    if category == "donation":
        needs = [n for n in needs if n in DONATION_ITEMS]

    constraints = {c: _any_word(t, w) for c, w in CONSTRAINT_WORDS.items()}
    # "free tutoring" is a description of the service, not a hard filter on a donation drop-off
    if category == "donation":
        constraints["free"] = False
    identity = [i for i, w in IDENTITY_WORDS.items() if _any_word(t, w)]
    campus = next((c for c, w in CAMPUS_WORDS.items() if _any_word(t, w)), None)
    if campus:
        constraints["near_campus"] = True
    urgent = _any_word(t, URGENT_WORDS)

    return {
        "category": category, "need_type": need_type, "needs": needs,
        "constraints": constraints, "identity": identity, "campus": campus,
        "urgent": urgent, "language": lang,
        "query_text": build_query_text(text, category, needs, lang), "source": "rules",
    }


# ----------------------------------------------------------------------------- LLM path
LLM_SYSTEM = f"""You convert a community member's request into a JSON object. Output ONLY JSON, no prose, no code fences.
Schema:
{{"category": one of {CATEGORIES} or null,
 "need_type": one of {NEED_TYPES} or null,
 "needs": array of tags from {sorted(NEEDS.keys())} (donation items for donate requests, service topics otherwise),
 "constraints": {{"free": bool, "open_now": bool, "open_24h": bool, "no_id": bool, "no_appointment": bool, "near_campus": bool}},
 "identity": array from {IDENTITIES},
 "campus": one of {CAMPUSES} or null,
 "urgent": true only for crisis/safety/homelessness/self-harm,
 "language": "zh" or "en" (language the user wrote in),
 "query_text": the request rewritten in short English keywords for semantic search}}
Rules: "donate/give away/get rid of" => category donation, need_type donate. Never invent needs the user did not mention.
Constraints are true only if the user states them. If unsure about a field use null / [] / false."""


def _extract_json(s: str) -> dict | None:
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _normalize_llm(raw: dict, rules: dict) -> dict:
    out = dict(rules)  # start from rules, override only with *valid* LLM values
    if raw.get("category") in CATEGORIES:
        out["category"] = raw["category"]
    if raw.get("need_type") in NEED_TYPES:
        out["need_type"] = raw["need_type"]
    if isinstance(raw.get("needs"), list):
        out["needs"] = list(dict.fromkeys(out["needs"] + [n for n in raw["needs"] if n in NEEDS]))
    if isinstance(raw.get("constraints"), dict):
        out["constraints"] = {c: bool(raw["constraints"].get(c, False)) or bool(rules["constraints"].get(c, False))
                              for c in CONSTRAINTS}
    if isinstance(raw.get("identity"), list):
        out["identity"] = list(dict.fromkeys(out["identity"] + [i for i in raw["identity"] if i in IDENTITIES]))
    if raw.get("campus") in CAMPUSES:
        out["campus"] = raw["campus"]
    out["urgent"] = bool(raw.get("urgent", rules["urgent"])) or rules["urgent"]  # never lose an emergency signal
    if raw.get("language") in ("zh", "en"):
        out["language"] = raw["language"]
    if isinstance(raw.get("query_text"), str) and raw["query_text"].strip():
        out["query_text"] = raw["query_text"].strip()[:300]
    out["source"] = "llm"
    return out


def parse_intent_llm(text: str, rules: dict) -> dict | None:
    if not config.GEMINI_API_KEY:
        return None
    try:
        from .llm import generate_text
        raw = _extract_json(generate_text(text[:1000], system_instruction=LLM_SYSTEM,
                                          max_output_tokens=500, json_mode=True))
        return _normalize_llm(raw, rules) if raw else None
    except Exception as e:  # network, quota, bad JSON ... never break the search
        print(f"[intent] Gemini failed, using rules: {e}")
        return None


_INTENT_CACHE: dict[str, tuple[float, dict]] = {}
_INTENT_CACHE_TTL = 600
_INTENT_CACHE_LIMIT = 512


def parse_intent(text: str, use_llm: bool | None = None) -> dict:
    rules = parse_intent_rules(text)
    use_llm = config.USE_LLM_INTENT if use_llm is None else use_llm
    if not use_llm or not config.GEMINI_API_KEY:
        return rules
    import time
    key = text.strip().casefold()
    cached = _INTENT_CACHE.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _INTENT_CACHE_TTL:
        return {**cached[1], "language": rules["language"]}
    parsed = parse_intent_llm(text, rules)
    if not parsed:
        return rules
    if len(_INTENT_CACHE) >= _INTENT_CACHE_LIMIT:
        oldest = min(_INTENT_CACHE, key=lambda item: _INTENT_CACHE[item][0])
        _INTENT_CACHE.pop(oldest, None)
    _INTENT_CACHE[key] = (now, parsed)
    return parsed
