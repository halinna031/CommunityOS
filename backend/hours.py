"""Tiny opening-hours engine.

Spec grammar (editable by humans, easy for an LLM to extract):
    "Mon-Fri 09:00-17:00; Sat 10:00-14:00,18:00-20:00"
    "Mon,Wed 10:00-12:00"
    "Mon-Sun 00:00-24:00"        (24/7)
    "By appointment" / ""        (unknown -> is_open = None)
"""
from __future__ import annotations
from datetime import datetime
import re

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_IDX = {d: i for i, d in enumerate(DAYS)}
_SEG = re.compile(r"^([A-Za-z,\-]+)\s+(\d{1,2}:\d{2}-\d{1,2}:\d{2}(?:,\d{1,2}:\d{2}-\d{1,2}:\d{2})*)$")


def _hm(s: str) -> int:
    h, m = s.strip().split(":")
    return int(h) * 60 + int(m)


def _days(part: str) -> list[int]:
    out: list[int] = []
    for tok in part.split(","):
        tok = tok.strip().lower()
        if "-" in tok:  # range, may wrap around the week (Fri-Mon)
            a, b = [t[:3] for t in tok.split("-")]
            k, j = _DAY_IDX[a], _DAY_IDX[b]
            while True:
                out.append(k)
                if k == j:
                    break
                k = (k + 1) % 7
        else:
            out.append(_DAY_IDX[tok[:3]])
    return out


def parse_hours(spec: str) -> dict[int, list[tuple[int, int]]]:
    table: dict[int, list[tuple[int, int]]] = {i: [] for i in range(7)}
    for seg in (spec or "").split(";"):
        seg = re.sub(r"\s*,\s*", ",", seg.strip())
        m = _SEG.match(seg)
        if not m:
            continue  # e.g. "By appointment"
        for d in _days(m.group(1)):
            for rng in m.group(2).split(","):
                a, b = rng.split("-")
                table[d].append((_hm(a), _hm(b)))
    return table


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def status_at(spec: str, now: datetime) -> dict:
    """Return {is_open: bool|None, closes_at, next_open: {day, time, offset_days}|None}."""
    table = parse_hours(spec)
    if not any(table.values()):
        return {"is_open": None, "closes_at": None, "next_open": None}
    today, mins = now.weekday(), now.hour * 60 + now.minute
    for a, b in sorted(table[today]):
        if a <= mins < b:
            return {"is_open": True, "closes_at": _fmt(b), "next_open": None}
    for offset in range(0, 8):  # next opening within a week
        d = (today + offset) % 7
        for a, _b in sorted(table[d]):
            if offset == 0 and a <= mins:
                continue
            return {"is_open": False, "closes_at": None,
                    "next_open": {"day": DAYS[d], "time": _fmt(a), "offset_days": offset}}
    return {"is_open": False, "closes_at": None, "next_open": None}


def weekly_table(spec: str) -> list[dict]:
    """For UI display: [{day:'mon', ranges:['09:00-17:00']}, ...]"""
    table = parse_hours(spec)
    return [{"day": DAYS[i], "ranges": [f"{_fmt(a)}-{_fmt(b)}" for a, b in sorted(table[i])]}
            for i in range(7)]
