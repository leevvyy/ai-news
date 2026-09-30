"""Living dashboard: one self-contained HTML page embedding the last N days as JSON.

The template (templates/dashboard.html) is split at `<!--BODY-->`:
  * artifact form: head part + body part, no document skeleton (the Artifact host wraps it)
  * site form:     a full <!doctype html> document for static hosting
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .aggregate import calendar_as_of, lang_split, rolling_week, trend, weekly_view
from .archive import Archive, week_bounds
from .config import COMPONENTS, REGIONS, TOPICS, Config

TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"
_MARK = "<!--BODY-->"


def payload(cfg: Config, archive: Archive, latest: date) -> dict[str, Any]:
    lo = latest - timedelta(days=cfg.dashboard_days - 1)
    issues: dict[str, Any] = {}
    for d, iss in archive.dailies_between(lo, latest):
        issues[d.isoformat()] = {
            "date": d.isoformat(),
            "window": iss["window"],
            "tldr": iss["tldr"],
            "notes": iss.get("notes", ""),
            "items": iss["items"],
            "calendar": calendar_as_of(cfg, archive, d),
            "week": rolling_week(cfg, archive, d),
            "langs": lang_split(iss["items"]),
            "md": f"{cfg.repo_url}/blob/main/issues/daily/{d.isoformat()}.md" if cfg.repo_url else "",
        }
    weeklies = {}
    for wid in archive.weekly_ids()[-8:]:
        if week_bounds(wid)[1] <= latest:
            v = weekly_view(cfg, archive, wid)
            v["md"] = f"{cfg.repo_url}/blob/main/issues/weekly/{wid}.md" if cfg.repo_url else ""
            weeklies[wid] = v
    return {
        "meta": {
            "title": cfg.site_title,
            "latest": latest.isoformat(),
            "utc_offset": cfg.utc_offset_hours,
            "tz_label": cfg.tz_label,
            "topics": [[k, v] for k, v in TOPICS.items()],
            "regions": dict(REGIONS),
            "components": list(COMPONENTS),
            "weights": dict(cfg.weights),
            "tiers": dict(cfg.tier_reliability),
            "kappa": cfg.breadth_kappa,
            "leads": cfg.leads,
            "briefs": cfg.briefs,
            "min_independent": cfg.min_independent,
            "max_leads_per_entity": cfg.max_leads_per_entity,
            "top_n": cfg.credibility_top_n,
            "baseline_days": cfg.momentum_baseline_days,
            "repo": cfg.repo_url,
        },
        "issues": issues,
        "trend": trend(archive, latest, cfg.dashboard_days + 29),
        "weeklies": weeklies,
    }


def _embed(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return raw.replace("</", "<\\/").replace("<!--", "<\\!--")


def render(cfg: Config, archive: Archive, latest: date, *, standalone: bool = False) -> str:
    data = payload(cfg, archive, latest)
    if standalone:  # the static site has an archive and a feed next to index.html; the artifact does not
        data["meta"]["site_links"] = {"Archive": "archive.html", "Atom feed": "feed.xml"}
    tpl = TEMPLATE.read_text("utf-8").replace("__PAYLOAD__", _embed(data))
    head, _, body = tpl.partition(_MARK)
    if not standalone:
        return head.rstrip() + "\n" + body.lstrip()
    feed = f'<link rel="alternate" type="application/atom+xml" href="feed.xml" title="{cfg.site_title}">\n'
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            f"{feed}{head.strip()}\n</head>\n<body>\n{body.strip()}\n</body>\n</html>\n")
