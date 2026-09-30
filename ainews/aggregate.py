"""Cross-issue aggregates: rolling week, weekly recap, 30-day trend, upcoming calendar."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from .archive import Archive, Item, daterange, week_bounds
from .config import LANGS, TOPICS, Config
from .dedupe import jaccard, title_tokens
from .market import item_events, mean_ci
from .scoring import rank_key


def topic_counts(items: Iterable[Item]) -> dict[str, int]:
    c = Counter(it["topic"] for it in items)
    return {k: c.get(k, 0) for k in TOPICS}


def lang_split(items: Iterable[Item]) -> dict[str, int]:
    c = Counter(s["lang"] for it in items for s in it["sources"])
    return {k: c.get(k, 0) for k in LANGS}


SECONDARY_WEIGHT = 0.25


def entity_board(cfg: Config, items: Iterable[Item], k: int = 8) -> list[dict[str, Any]]:
    """Entities ranked by attribution-weighted Σ I: the primary entity of an item gets its
    full score, every other named entity 0.25 of it (each entity counted once per item)."""
    weight: defaultdict[str, float] = defaultdict(float)
    mentions: Counter[str] = Counter()
    for it in items:
        names = [cfg.canonical_entity(x) for x in it["entities"]]
        for i, e in enumerate(dict.fromkeys(names)):
            weight[e] += it["score"]["total"] * (1.0 if i == 0 else SECONDARY_WEIGHT)
            mentions[e] += 1
    ranked = sorted(weight, key=lambda e: (-weight[e], -mentions[e], e))[:k]
    return [{"entity": e, "mentions": mentions[e], "weight": round(weight[e], 1)} for e in ranked]


def ref(d: date | None, it: Item) -> dict[str, Any]:
    """Compact pointer to an item, for sidebars and weekly tables."""
    return {"date": d.isoformat() if d else None, "id": it["id"], "title": it["title"],
            "topic": it["topic"], "region": it["region"], "score": it["score"]["total"],
            "url": it["sources"][0]["url"], "outlet": it["sources"][0]["outlet"]}


def rolling_week(cfg: Config, archive: Archive, d: date) -> dict[str, Any]:
    """The daily issue's 'This week so far' panel: the 7 days ending on d."""
    lo = d - timedelta(days=6)
    pool = [(dd, it) for dd, it in archive.items_between(lo, d) if "score" in it]
    today_leads = {it["id"] for dd, it in pool if dd == d and it.get("placement") == "lead"}
    others = sorted(((dd, it) for dd, it in pool if it["id"] not in today_leads),
                    key=lambda p: rank_key(p[1]))
    items = [it for _, it in pool]
    return {
        "from": lo.isoformat(), "to": d.isoformat(),
        "days": len({dd for dd, _ in pool}), "n": len(items),
        "top": [ref(dd, it) for dd, it in others[:5]],
        "topics": topic_counts(items),
        "entities": entity_board(cfg, items, 6),
    }


def trend(archive: Archive, end: date, days: int) -> list[dict[str, Any]]:
    """Per-day topic counts for [end-days+1, end]; days before the archive starts are omitted,
    gaps inside it are kept with `missing: true` so charts show them honestly."""
    have = archive.daily_dates()
    if not have:
        return []
    start = max(end - timedelta(days=days - 1), have[0])
    out = []
    for d in daterange(start, end):
        iss = archive.daily(d)
        if iss is None:
            out.append({"date": d.isoformat(), "counts": topic_counts([]), "missing": True})
        else:
            out.append({"date": d.isoformat(), "counts": topic_counts(iss["items"]), "missing": False})
    return out


def _event_key(ev: dict) -> str:
    return ev["date"] if len(ev["date"]) == 10 else ev["date"] + "-32"  # month-only → end of month


def calendar_as_of(cfg: Config, archive: Archive, d: date) -> list[dict[str, Any]]:
    """Upcoming events announced in the last 30 days that are still ahead of d.
    Later mentions of the same event (same date, title J ≥ 0.6) replace earlier ones."""
    merged: list[dict[str, Any]] = []
    sources: list[tuple[date, list[dict]]] = [
        (dd, iss.get("upcoming", [])) for dd, iss in archive.dailies_between(d - timedelta(days=30), d)]
    for wid in archive.weekly_ids():
        mon, sun = week_bounds(wid)
        if d - timedelta(days=30) <= sun <= d:
            sources.append((sun, (archive.weekly(wid) or {}).get("upcoming", [])))
    sources.sort(key=lambda p: p[0])
    for seen_on, events in sources:
        for ev in events:
            tok = title_tokens(ev["title"])
            for i, old in enumerate(merged):
                if old["date"] == ev["date"] and jaccard(tok, title_tokens(old["title"])) >= 0.6:
                    merged[i] = {**ev, "first_seen": old["first_seen"]}
                    break
            else:
                merged.append({**ev, "first_seen": seen_on.isoformat()})
    today, horizon = d.isoformat(), (d + timedelta(days=cfg.calendar_horizon_days)).isoformat()
    keep = [ev for ev in merged if _event_key(ev) >= today and ev["date"][:10] <= horizon]
    return sorted(keep, key=lambda ev: (_event_key(ev), ev["title"]))


def weekly_view(cfg: Config, archive: Archive, wid: str) -> dict[str, Any]:
    mon, sun = week_bounds(wid)
    pool = [(d, it) for d, it in archive.items_between(mon, sun) if "score" in it]
    ranked = sorted(pool, key=lambda p: rank_key(p[1]))
    items = [it for _, it in pool]
    by_id = {it["id"]: (d, it) for d, it in pool}
    wk = archive.weekly(wid) or {}
    themes = [{**th, "items": [ref(*by_id[i]) for i in th.get("item_ids", []) if i in by_id]}
              for th in wk.get("themes", [])]
    per_day = []
    for d in daterange(mon, sun):
        day_items = [it for dd, it in pool if dd == d]
        per_day.append({"date": d.isoformat(), "counts": topic_counts(day_items),
                        "issue": archive.daily(d) is not None})
    return {
        "week": wid, "start": mon.isoformat(), "end": sun.isoformat(),
        "headline": wk.get("headline", ""), "themes": themes,
        "top": [ref(d, it) for d, it in ranked[:10]],
        "topics": topic_counts(items), "langs": lang_split(items),
        "entities": entity_board(cfg, items, 8),
        "per_day": per_day, "n": len(items),
        "daily_issues": sum(1 for p in per_day if p["issue"]),
        "backfilled": bool(wk.get("items")),
        "market": weekly_market(cfg, archive, pool),
    }


def market_for(cfg: Config, archive: Archive, items: Iterable[Item]) -> dict[str, list[dict]]:
    """item id → event-study results for its tickers (computed from data/market/prices.csv)."""
    return {it["id"]: ev for it in items if (ev := item_events(cfg.market, archive.prices, it))}


def china_desk(items: Iterable[Item]) -> list[Item]:
    return [it for it in items if it["region"] == "cn"]


def weekly_market(cfg: Config, archive: Archive, pool: list[tuple[date, Item]]) -> dict[str, Any]:
    """Mean CAR by topic (95% t-interval) over completed events of the week, plus the largest |t|.

    An event is a unique (symbol, day 0): several stories naming the same stock on the same day
    share one abnormal return, so counting them separately would inflate n. Within a topic each
    event counts once; its story is the highest-scoring one that named it."""
    events: dict[tuple[str, str, str], tuple[dict, date, Item]] = {}
    for d, it in sorted(pool, key=lambda p: rank_key(p[1])):
        for ev in item_events(cfg.market, archive.prices, it):
            if ev["status"] == "ok":
                events.setdefault((it["topic"], ev["symbol"], ev["day0"]), (ev, d, it))
    by_topic: defaultdict[str, list[float]] = defaultdict(list)
    rows = []
    for (topic, _, _), (ev, d, it) in sorted(events.items()):
        by_topic[topic].append(ev["car"])
        rows.append({**ref(d, it), "symbol": ev["symbol"], "car": ev["car"], "t": ev["t"], "day0": ev["day0"]})
    topics = [{"topic": k, **ci} for k in TOPICS if (ci := mean_ci(by_topic.get(k, [])))]
    unique = {(r["symbol"], r["day0"]): r for r in rows}
    top = sorted(unique.values(), key=lambda r: (-abs(r["t"] or 0.0), r["symbol"], r["day0"]))
    return {"n_events": len(unique), "by_topic": topics, "top": top[:8]}
