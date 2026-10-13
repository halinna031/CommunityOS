"""Step 5-6: turn structured signals into human text (zh/en).

Facts come ONLY from the database + scoring signals. Optional LLM polish may reword the summary,
but a guard rejects any output that contains digits/phone numbers/addresses not present in the facts.
"""
from __future__ import annotations
import re

from . import config

DAY = {"zh": {"mon": "周一", "tue": "周二", "wed": "周三", "thu": "周四", "fri": "周五", "sat": "周六", "sun": "周日"},
       "en": {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}}
ITEM = {"zh": {"furniture": "家具", "electronics": "电子产品", "clothing": "衣物", "books": "书籍", "kitchenware": "厨具",
               "bedding": "床上用品", "bikes": "自行车", "toys": "玩具", "food": "食品", "mattress": "床垫",
               "math": "数学", "english": "英语", "writing": "写作", "coding": "编程", "resume": "简历", "science": "理科",
               "homework": "作业辅导", "hot_meals": "热餐", "halal": "清真食品", "baby_supplies": "婴儿用品",
               "produce": "新鲜蔬果", "groceries": "食品杂货", "quiet_study": "安静自习", "group_rooms": "讨论室", "wifi": "Wi-Fi", "computer_skills": "电脑技能"},
        "en": {"furniture": "furniture", "electronics": "electronics", "clothing": "clothing", "books": "books",
               "kitchenware": "kitchenware", "bedding": "bedding", "bikes": "bikes", "toys": "toys", "food": "food",
               "mattress": "mattresses", "math": "math", "english": "English", "writing": "writing", "coding": "coding",
               "resume": "resumes", "science": "science", "homework": "homework help", "hot_meals": "hot meals",
               "halal": "halal food", "baby_supplies": "baby supplies", "produce": "fresh produce", "groceries": "groceries",
               "quiet_study": "quiet study", "group_rooms": "group rooms", "wifi": "Wi-Fi", "computer_skills": "computer skills"}}
WHO = {"zh": {"students": "在校学生", "newcomers": "新移民", "low_income": "低收入人群", "seniors": "老年人",
              "student": "在校学生", "newcomer": "新移民", "senior": "老年人"},
       "en": {"students": "students", "newcomers": "newcomers", "low_income": "low-income households", "seniors": "seniors",
              "student": "students", "newcomer": "newcomers", "senior": "seniors"}}
CATEGORY = {"zh": {"food_assistance": "食物援助", "donation": "物品捐赠", "learning": "免费学习/辅导", "study_space": "自习空间"},
            "en": {"food_assistance": "food assistance", "donation": "donations", "learning": "free learning help", "study_space": "study spaces"}}


def _items(lang: str, keys: list[str]) -> str:
    sep = "、" if lang == "zh" else ", "
    return sep.join(ITEM[lang].get(k, k) for k in keys)


def _who(lang: str, keys: list[str]) -> str:
    sep = "、" if lang == "zh" else ", "
    return sep.join(WHO[lang].get(k, k) for k in keys)


def render_signal(sig: dict, lang: str) -> str:
    c, p, zh = sig["code"], sig["p"], lang == "zh"
    if c == "has_needs":
        if p.get("donation"):
            return f"明确接收：{_items(lang, p['items'])}" if zh else f"Explicitly accepts: {_items(lang, p['items'])}"
        return f"提供：{_items(lang, p['items'])}" if zh else f"Offers: {_items(lang, p['items'])}"
    if c == "unconfirmed_needs":
        return f"未明确说明是否提供：{_items(lang, p['items'])}" if zh else f"Doesn't confirm: {_items(lang, p['items'])}"
    if c == "category_ok":
        return f"属于「{CATEGORY['zh'][p['category']]}」类" if zh else f"Category: {CATEGORY['en'][p['category']]}"
    if c == "open_to_all":
        return "对所有人开放" if zh else "Open to everyone"
    if c == "eligible":
        return f"你符合资格（面向{_who(lang, p['who'])}）" if zh else f"You qualify (for {_who(lang, p['who'])})"
    if c == "restricted_unknown":
        return f"仅限{_who(lang, p['who'])}，请确认你是否符合" if zh else f"Limited to {_who(lang, p['who'])}; check you qualify"
    if c == "restricted":
        return f"仅限{_who(lang, p['who'])}，你可能不符合" if zh else f"Only for {_who(lang, p['who'])}; you may not qualify"
    if c == "speaks_zh":
        return "提供中文服务" if zh else "Mandarin-speaking staff/support"
    if c == "distance":
        base = "校园" if p["from_campus"] else ("所选地区中心" if p.get("from_region") else "你")
        if zh:
            return f"距{base}约 {p['km']} 公里，步行约 {p['walk_min']} 分钟"
        origin = "campus" if p["from_campus"] else ("the selected area's center" if p.get("from_region") else "you")
        return f"{p['km']} km from {origin}, about {p['walk_min']} min walk"
    if c == "open_now":
        return f"现在营业，{p['closes']} 关门" if zh else f"Open now, closes {p['closes']}"
    if c == "closed_now":
        n = p.get("next")
        if not n:
            return "现在未营业" if zh else "Closed now"
        when = ("今天" if zh else "today") if n["offset_days"] == 0 else DAY[lang][n["day"]]
        return f"现在未营业，{when} {n['time']} 开门" if zh else f"Closed now, opens {when} {n['time']}"
    if c == "hours_unknown":
        return "营业时间不确定，请先联系" if zh else "Hours unclear; contact first"
    if c == "verified":
        d = p["days"]
        return f"{d} 天前核实" if zh else f"Verified {d} days ago"
    if c == "reports_pending":
        return f"{p['n']} 位用户反馈信息可能已过时" if zh else f"{p['n']} user report(s) say this may be outdated"
    if c == "needs_appointment":
        return "需要预约" if zh else "Appointment required"
    if c == "walk_in":
        return "无需预约，可直接前往" if zh else "No appointment needed"
    if c == "needs_id":
        return "需要出示证件" if zh else "ID required"
    if c == "has_fee":
        return f"收费 ${p['fee']:g}" if zh else f"Fee: ${p['fee']:g}"
    if c == "violates_open_now":
        return "不符合你的「现在营业」要求" if zh else "Doesn't meet your 'open now' request"
    if c == "violates_24h":
        return "不是24小时开放" if zh else "Not open 24 hours"
    if c == "violates_no_id":
        return "不符合你的「无需证件」要求" if zh else "Doesn't meet your 'no ID' request"
    if c == "violates_no_appt":
        return "不符合你的「无需预约」要求" if zh else "Doesn't meet your 'no appointment' request"
    return c


# signals worth showing, ordered by importance; "walk_in"/"open_to_all" are noise unless everything else is empty
_PRIORITY = ["has_needs", "category_ok", "eligible", "open_now", "distance", "speaks_zh", "walk_in", "open_to_all", "verified"]


def build_summary(res: dict, lang: str) -> str:
    sigs = res["rank_info"]["signals"]
    ok = [s for s in sigs if s["level"] == "ok"]
    ok.sort(key=lambda s: _PRIORITY.index(s["code"]) if s["code"] in _PRIORITY else 99)
    warn = [s for s in sigs if s["level"] in ("warn", "bad") and s["code"] not in ("verified",)]
    parts = [render_signal(s, lang) for s in ok[:3]]
    head = ("；".join(parts) if lang == "zh" else "; ".join(parts))
    tail = ""
    if warn:
        w = [render_signal(s, lang) for s in warn[:2]]
        tail = ("。注意：" + "；".join(w)) if lang == "zh" else (". Heads-up: " + "; ".join(w))
    return head + tail + ("。" if lang == "zh" else ".")


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.\-]\d+)*", text))


def llm_polish(summary: str, res: dict, lang: str) -> str:
    """Optional: ask Gemini to make the summary read naturally; reject added numbers/facts."""
    if not (config.USE_LLM_EXPLAIN and config.GEMINI_API_KEY):
        return summary
    try:
        from .llm import generate_text
        facts = f"{res['name']} | {summary}"
        out = generate_text(
            facts, max_output_tokens=200,
            system_instruction=("Rewrite the facts as ONE friendly sentence in " + ("Chinese" if lang == "zh" else "English") +
                               ". Use ONLY the given facts. Do not add numbers, addresses, phone numbers or any new claim."),
        )
        return out if out and _numbers(out) <= _numbers(facts) else summary
    except Exception as e:
        print(f"[explain] polish failed: {e}")
        return summary


def explain_result(res: dict, lang: str) -> dict:
    summary = llm_polish(build_summary(res, lang), res, lang)
    return {
        "summary": summary,
        "signals": [{"level": s["level"], "text": render_signal(s, lang), "code": s["code"]} for s in res["rank_info"]["signals"]],
    }


EMERGENCY = {
    "zh": {"title": "如果你现在处于危险或危机中",
           "lines": ["紧急危险：拨打 911", "心理危机/自杀倾向：拨打或发短信 988（加拿大全国，全天候）", "找各类社区救助服务：拨打 211（或访问 211ontario.ca）"]},
    "en": {"title": "If you are in danger or in crisis right now",
           "lines": ["Immediate danger: call 911", "Suicide/crisis support: call or text 988 (Canada-wide, 24/7)", "Find community services: dial 211 (or visit 211ontario.ca)"]},
}
FALLBACK = {
    "zh": "没有找到可靠匹配的资源。我们不会硬凑答案——你可以拨打 211（或访问 211ontario.ca）让工作人员帮你查找，也可以换个说法再试一次。",
    "en": "We couldn't find a reliable match, and we won't pad the list with guesses. You can dial 211 (or visit 211ontario.ca) for a person to help, or try rephrasing.",
}
