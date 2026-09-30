"""Structural validation of research JSON. Errors block the build; warnings are printed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .archive import parse_ts
from .config import CONFIDENCE, LANGS, REGIONS, TIERS, TOPICS, UPCOMING_KINDS, Config
from .timewin import in_window

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_URL = re.compile(r"^https?://[^\s/$.?#][^\s]*$")
_DAY = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: "Report") -> None:
        self.errors += other.errors
        self.warnings += other.warnings


def _str(r: Report, where: str, obj: dict, key: str, *, max_len: int | None = None,
         required: bool = True) -> None:
    v = obj.get(key)
    if v is None or v == "":
        if required:
            r.err(where, f"missing '{key}'")
        return
    if not isinstance(v, str):
        r.err(where, f"'{key}' must be a string")
    elif max_len and len(v) > max_len:
        r.warn(where, f"'{key}' is {len(v)} chars (> {max_len})")


def _unit(r: Report, where: str, obj: dict, key: str) -> None:
    v = obj.get(key)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.0 <= v <= 1.0:
        r.err(where, f"'{key}' must be a number in [0, 1], got {v!r}")


def validate_item(cfg: Config, it: Any, where: str, window: dict | None) -> Report:
    r = Report()
    if not isinstance(it, dict):
        r.err(where, "item must be an object")
        return r
    iid = it.get("id", "")
    where = f"{where}[{iid or '?'}]"
    if not isinstance(iid, str) or not _ID.match(iid):
        r.err(where, "'id' must be kebab-case [a-z0-9-]")
    _str(r, where, it, "title", max_len=140)
    _str(r, where, it, "summary")
    _str(r, where, it, "why_it_matters")
    _str(r, where, it, "original_title", required=False)
    _str(r, where, it, "confidence_note", required=False)
    _str(r, where, it, "follow_up_of", required=False)
    if it.get("topic") not in TOPICS:
        r.err(where, f"'topic' must be one of {list(TOPICS)}")
    if it.get("region") not in REGIONS:
        r.err(where, f"'region' must be one of {list(REGIONS)}")
    for key in ("entities", "key_numbers", "tickers"):
        v = it.get(key, [] if key != "entities" else None)
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            r.err(where, f"'{key}' must be a list of non-empty strings")
    if not it.get("entities"):
        r.err(where, "'entities' needs at least the primary entity")
    if isinstance(it.get("key_numbers"), list) and len(it["key_numbers"]) > 6:
        r.warn(where, f"{len(it['key_numbers'])} key_numbers; keep the 6 that matter most")
    _unit(r, where, it, "impact")
    _unit(r, where, it, "novelty")
    cov = it.get("coverage")
    if not isinstance(cov, int) or isinstance(cov, bool) or cov < 1:
        r.err(where, "'coverage' must be a positive integer")

    try:
        parse_ts(it.get("published_at", ""))
    except (ValueError, TypeError, AttributeError):
        r.err(where, "'published_at' must be an ISO-8601 timestamp with timezone")
    else:
        if window:
            inside, graced = in_window(it["published_at"], window["start"], window["end"], cfg.grace_hours)
            if not graced:
                r.err(where, f"published_at {it['published_at']} is outside the window "
                             f"(+{cfg.grace_hours}h grace); drop it or set follow_up_of")
            elif not inside:
                r.warn(where, f"published_at {it['published_at']} is inside the grace period only")

    sources = it.get("sources")
    if not isinstance(sources, list) or not sources:
        r.err(where, "'sources' must be a non-empty list")
        return r
    has_zh = False
    for j, s in enumerate(sources):
        sw = f"{where}.sources[{j}]"
        if not isinstance(s, dict):
            r.err(sw, "source must be an object")
            continue
        if not isinstance(s.get("url"), str) or not _URL.match(s["url"]):
            r.err(sw, "'url' must be an http(s) URL")
            continue
        _str(r, sw, s, "outlet")
        if s.get("tier") not in TIERS:
            r.err(sw, f"'tier' must be one of {list(TIERS)}")
        if s.get("lang") not in LANGS:
            r.err(sw, f"'lang' must be one of {list(LANGS)}")
        has_zh |= s.get("lang") == "zh"
        known = cfg.known_domain(s["url"])
        if known and s.get("tier") in TIERS and known.tier != s["tier"] and known.tier != "primary":
            r.warn(sw, f"{known.name} is usually tier '{known.tier}', item says '{s['tier']}'")
    if has_zh and not it.get("original_title"):
        r.warn(where, "Chinese-sourced item without 'original_title'")
    if it["sources"] and all(s.get("tier") == "social" for s in sources if isinstance(s, dict)):
        r.warn(where, "only social sources; the item can never be a lead")
    return r


def validate_upcoming(ev: Any, where: str, today: date | None) -> Report:
    r = Report()
    if not isinstance(ev, dict):
        r.err(where, "event must be an object")
        return r
    if not isinstance(ev.get("date"), str) or not _DAY.match(ev["date"]):
        r.err(where, "'date' must be YYYY-MM-DD or YYYY-MM")
    elif today and len(ev["date"]) == 10 and date.fromisoformat(ev["date"]) < today - timedelta(days=1):
        r.warn(where, f"event date {ev['date']} is in the past")
    _str(r, where, ev, "title", max_len=110)
    if ev.get("kind") not in UPCOMING_KINDS:
        r.err(where, f"'kind' must be one of {list(UPCOMING_KINDS)}")
    if ev.get("confidence") not in CONFIDENCE:
        r.err(where, f"'confidence' must be one of {list(CONFIDENCE)}")
    if ev.get("url") is not None and not (isinstance(ev["url"], str) and _URL.match(ev["url"])):
        r.err(where, "'url' must be an http(s) URL")
    return r


def validate_daily(cfg: Config, issue: Any, expected_date: date | None = None) -> Report:
    r = Report()
    if not isinstance(issue, dict):
        r.err("issue", "top level must be an object")
        return r
    try:
        d = date.fromisoformat(issue.get("date", ""))
    except (ValueError, TypeError):
        r.err("issue", "'date' must be YYYY-MM-DD")
        d = None
    if expected_date and d and d != expected_date:
        r.err("issue", f"'date' {d} does not match file name {expected_date}")
    win = issue.get("window")
    try:
        s, e = parse_ts(win["start"]), parse_ts(win["end"])
        if not s < e:
            r.err("issue.window", "start must be before end")
        elif (e - s) > timedelta(hours=cfg.max_window_hours):
            r.err("issue.window", f"window longer than {cfg.max_window_hours}h")
    except (TypeError, KeyError, ValueError):
        r.err("issue.window", "needs ISO 'start' and 'end'")
        win = None
    tldr = issue.get("tldr")
    if not isinstance(tldr, list) or not 1 <= len(tldr) <= 4 or not all(isinstance(t, str) and t for t in tldr):
        r.err("issue", "'tldr' must be 1-4 non-empty strings")
    items = issue.get("items")
    if not isinstance(items, list) or not items:
        r.err("issue", "'items' must be a non-empty list")
        items = []
    for i, it in enumerate(items):
        r.extend(validate_item(cfg, it, f"items[{i}]", win))
    for i, ev in enumerate(issue.get("upcoming", [])):
        r.extend(validate_upcoming(ev, f"upcoming[{i}]", d))
    n_ok = sum(1 for it in items if isinstance(it, dict) and it.get("sources"))
    if n_ok and n_ok < cfg.leads + 3:
        r.warn("issue", f"only {n_ok} items; aim for >= {cfg.leads + cfg.briefs}")
    return r


def validate_weekly(cfg: Config, weekly: Any, wid: str) -> Report:
    r = Report()
    if not isinstance(weekly, dict):
        r.err("weekly", "top level must be an object")
        return r
    if weekly.get("week") != wid:
        r.err("weekly", f"'week' must equal file name {wid}")
    _str(r, "weekly", weekly, "headline", max_len=240)
    themes = weekly.get("themes", [])
    if not isinstance(themes, list):
        r.err("weekly", "'themes' must be a list")
        themes = []
    if not themes:
        r.warn("weekly", "no themes yet; RUNBOOK step 5 asks for 3-4")
    for i, th in enumerate(themes):
        tw = f"weekly.themes[{i}]"
        _str(r, tw, th, "title", max_len=60)
        _str(r, tw, th, "summary")
        if not isinstance(th.get("item_ids", []), list):
            r.err(tw, "'item_ids' must be a list")
    for i, it in enumerate(weekly.get("items", [])):
        r.extend(validate_item(cfg, it, f"weekly.items[{i}]", None))
    for i, ev in enumerate(weekly.get("upcoming", [])):
        r.extend(validate_upcoming(ev, f"weekly.upcoming[{i}]", None))
    return r
