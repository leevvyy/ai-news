r"""Importance score and placement.

    I = 100 · Σ_k w_k · s_k,   s_k ∈ [0, 1],   Σ_k w_k = 1

    s_impact, s_novelty   rubric judgements recorded by the researcher (RUNBOOK.md §3)
    s_credibility = 1 − Π_{j ≤ n} (1 − r_{tier(j)})   noisy-OR over the n most reliable distinct domains
    s_breadth     = 1 − exp(−c / κ)                    c = independent outlets covering the story
    s_momentum    = 1 − exp(−m_t / (μ₇ + 1))           m_t = items in this issue on the same primary entity,
                                                       μ₇  = that entity's mean items/day over the
                                                             previous 7 archived days

Momentum is an attention burst relative to the entity's own baseline: a lab with
four stories today and ~0 on a normal day scores near 1; a lab that is in the news
every day needs a bigger burst to score the same.

Placement: sort by (−I, −s_credibility, id); the first `leads` *verified* items
become leads, skipping any whose primary entity already has `max_leads_per_entity`
leads (a diversity constraint); the next `briefs` items are briefs, the rest are
"noted" (kept for the weekly pool). Verified ⇔ ≥1 primary source, or
≥ `min_independent` distinct non-social outlets.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta
from typing import Iterable, Mapping

from .archive import Archive, Item, parse_ts
from .config import COMPONENTS, Config
from .dedupe import domain_of


def credibility(sources: Iterable[dict], reliability: Mapping[str, float], top_n: int = 3) -> float:
    best: dict[str, float] = {}
    for s in sources:
        key = domain_of(s["url"])
        best[key] = max(best.get(key, 0.0), reliability[s["tier"]])
    top = sorted(best.values(), reverse=True)[:top_n]
    return 1.0 - math.prod(1.0 - r for r in top)


def breadth(coverage: int, kappa: float) -> float:
    return 1.0 - math.exp(-coverage / kappa)


def momentum(mentions_today: int, baseline: float) -> float:
    return 1.0 - math.exp(-mentions_today / (baseline + 1.0))


def effective_coverage(item: Item) -> int:
    return max(int(item.get("coverage", 0)), len({domain_of(s["url"]) for s in item["sources"]}))


def is_verified(item: Item, min_independent: int) -> bool:
    if any(s["tier"] == "primary" for s in item["sources"]):
        return True
    independent = {domain_of(s["url"]) for s in item["sources"] if s["tier"] != "social"}
    return len(independent) >= min_independent


def entity_baseline(cfg: Config, archive: Archive, entity: str, on: date) -> float:
    """μ₇: mean items/day mentioning `entity` over archived days in [on−7, on−1].

    Days absent from the archive are skipped rather than counted as zero, so the
    first week of a fresh archive does not inflate momentum."""
    target = cfg.canonical_entity(entity).casefold()
    per_day: Counter[date] = Counter()
    days: set[date] = set()
    lo, hi = on - timedelta(days=cfg.momentum_baseline_days), on - timedelta(days=1)
    for d in archive.daily_dates():
        if lo <= d <= hi:
            days.add(d)
    for d, it in archive.items_between(lo, hi):
        days.add(d)
        if any(cfg.canonical_entity(e).casefold() == target for e in it.get("entities", [])):
            per_day[d] += 1
    return sum(per_day.values()) / len(days) if days else 0.0


def mentions(cfg: Config, entity: str, cohort: Iterable[Item]) -> int:
    """m_t: items in the cohort (this issue) that mention `entity`."""
    target = cfg.canonical_entity(entity).casefold()
    return sum(1 for it in cohort if any(cfg.canonical_entity(e).casefold() == target for e in it["entities"]))


def score_item(cfg: Config, archive: Archive, item: Item, on: date, cohort: list[Item] | None = None) -> dict:
    cov = effective_coverage(item)
    mu = entity_baseline(cfg, archive, item["entities"][0], on)
    m_t = max(1, mentions(cfg, item["entities"][0], cohort if cohort is not None else [item]))
    s = {
        "impact": float(item["impact"]),
        "novelty": float(item["novelty"]),
        "credibility": credibility(item["sources"], cfg.tier_reliability, cfg.credibility_top_n),
        "breadth": breadth(cov, cfg.breadth_kappa),
        "momentum": momentum(m_t, mu),
    }
    total = 100.0 * sum(cfg.weights[k] * s[k] for k in COMPONENTS)
    return {
        "total": round(total, 1),
        "components": {k: round(s[k], 3) for k in COMPONENTS},
        "coverage": cov,
        "mentions": m_t,
        "baseline": round(mu, 3),
        "verified": is_verified(item, cfg.min_independent),
    }


def rank_key(item: Item) -> tuple[float, float, str]:
    sc = item["score"]
    return (-sc["total"], -sc["components"]["credibility"], item["id"])


def score_and_place(cfg: Config, archive: Archive, items: list[Item], on: date) -> list[Item]:
    """Mutates items (adds score/rank/placement) and returns them in rank order."""
    for it in items:
        it["score"] = score_item(cfg, archive, it, on, items)
    ranked = sorted(items, key=rank_key)
    leads = 0
    per_entity: Counter[str] = Counter()
    rest: list[Item] = []
    for it in ranked:
        ent = cfg.canonical_entity(it["entities"][0]).casefold()
        if leads < cfg.leads and it["score"]["verified"] and per_entity[ent] < cfg.max_leads_per_entity:
            it["placement"] = "lead"
            leads += 1
            per_entity[ent] += 1
        else:
            rest.append(it)
    for i, it in enumerate(rest):
        it["placement"] = "brief" if i < cfg.briefs else "noted"
    order = {"lead": 0, "brief": 1, "noted": 2}
    ranked.sort(key=lambda it: (order[it["placement"]], rank_key(it)))
    for i, it in enumerate(ranked, 1):
        it["rank"] = i
    return ranked


def item_local_date(cfg: Config, item: Item) -> date:
    return parse_ts(item["published_at"]).astimezone(cfg.tz).date()
