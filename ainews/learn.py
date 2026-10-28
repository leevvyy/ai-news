"""Learning track: which capability gaps today's news is a good hook for, and how the track is going.

    priority_d = (target_d - now_d) * w_d                          (learning.toml)
    h_d        = sum_i I_i / 100  over today's items i that match a keyword of gap d
    hook_d     = 1 - exp(-h_d)                                      (saturates: news can at most double a gap's rank)
    damp_d     = 1 - exp(-Δ_d / τ)                                  (Δ_d = days since gap d was last taught; 1 if never)
    S_d        = priority_d * (1 + hook_d) * damp_d

`suggest` ranks gaps by S_d for the researcher, who then writes the issue's `learn` entries
(explanation + Python snippet). `tracker` summarises what was taught over the last N days.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from .archive import Archive, Item
from .config import Config, Gap


@lru_cache(maxsize=None)
def _pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword))  # word start; "eval" hits "evaluation"


def _text(it: Item) -> str:
    return " ".join(str(it.get(k, "")) for k in ("title", "summary", "why_it_matters")).casefold()


def matches(gap: Gap, it: Item) -> list[str]:
    text = _text(it)
    return [k for k in gap.keywords if _pattern(k).search(text)]


def _weight(it: Item) -> float:
    sc = it.get("score")
    return sc["total"] / 100 if sc else float(it.get("impact", 0.0))


def last_taught(archive: Archive, before: date) -> dict[str, date]:
    """Most recent issue date (strictly before `before`) that carried a learn item per gap."""
    out: dict[str, date] = {}
    for d in archive.daily_dates():
        if d >= before:
            break
        for le in (archive.daily(d) or {}).get("learn", []):
            out[le["gap"]] = d
    return out


def suggest(cfg: Config, archive: Archive, d: date, items: list[Item]) -> list[dict[str, Any]]:
    tau = float(cfg.learn.get("recency_tau_days", 4.0))
    last = last_taught(archive, d)
    rows = []
    for g in cfg.gaps.values():
        hooks = [(it, m) for it in items if (m := matches(g, it))]
        hooks.sort(key=lambda p: -_weight(p[0]))
        hook = 1.0 - math.exp(-sum(_weight(it) for it, _ in hooks))
        since = (d - last[g.id]).days if g.id in last else None
        damp = 1.0 if since is None else 1.0 - math.exp(-since / tau)
        rows.append({
            "gap": g.id, "label": g.label, "now": g.now, "target": g.target, "priority": round(g.priority, 3),
            "hook": round(hook, 3), "days_since": since, "damp": round(damp, 3),
            "score": round(g.priority * (1 + hook) * damp, 3),
            "hooks": [{"id": it["id"], "title": it["title"], "keywords": m} for it, m in hooks[:3]],
        })
    rows.sort(key=lambda r: (-r["score"], -r["priority"], r["gap"]))
    return rows


def tracker(cfg: Config, archive: Archive, d: date) -> dict[str, Any]:
    """Per-gap counts over the last `tracker_days` issues up to d, plus the issue streak."""
    days = int(cfg.learn.get("tracker_days", 30))
    lo = d - timedelta(days=days - 1)
    per = {g: {"gap": g, "label": x.label, "short": x.short, "now": x.now, "target": x.target, "priority": round(x.priority, 3),
               "n": 0, "minutes": 0, "last": None} for g, x in cfg.gaps.items()}
    total = 0
    for day, iss in archive.dailies_between(lo, d):
        for le in iss.get("learn", []):
            row = per.get(le["gap"])
            if row is None:
                continue
            row["n"] += 1
            row["minutes"] += int(le.get("minutes", 0))
            row["last"] = day.isoformat()
            total += 1
    streak = 0
    for day in reversed([x for x in archive.daily_dates() if x <= d]):
        if not (archive.daily(day) or {}).get("learn"):
            break
        streak += 1
    rows = sorted(per.values(), key=lambda r: (-r["priority"], r["gap"]))
    return {"from": lo.isoformat(), "to": d.isoformat(), "days": days, "total": total, "streak": streak,
            "minutes": sum(r["minutes"] for r in rows), "gaps": rows}
