"""Synthetic fixtures (example.* domains only) and a throwaway repo root."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from ainews.config import ROOT, load_config


def make_item(iid: str, *, title: str | None = None, topic: str = "models", entity: str = "LabA",
              tiers: tuple[str, ...] = ("primary", "press"), coverage: int = 3, impact: float = 0.6,
              novelty: float = 0.5, published_at: str = "2026-09-29T10:00:00Z", **extra) -> dict:
    sources = [{"url": f"https://{tier}{i}.example.com/{iid}", "outlet": f"{tier.title()} {i}",
                "lang": "en", "tier": tier, "title": title or iid} for i, tier in enumerate(tiers)]
    return {"id": iid, "title": title or f"{entity} story {iid}", "summary": "Something happened. More detail.",
            "why_it_matters": "It matters.", "key_numbers": [], "topic": topic, "region": "us",
            "entities": [entity], "tickers": [], "published_at": published_at, "sources": sources,
            "coverage": coverage, "impact": impact, "novelty": novelty, **extra}


def make_issue(day: str, items: list[dict], *, start: str | None = None, end: str | None = None) -> dict:
    return {"date": day,
            "window": {"start": start or f"{day}T00:00:00Z", "end": end or f"{day}T23:59:00Z"},
            "tldr": ["one", "two", "three"], "items": items, "upcoming": [], "notes": ""}


class TempRepo:
    """Copies config.toml + sources.toml into a temp dir with an empty archive."""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="ainews-test-"))
        for name in ("config.toml", "sources.toml"):
            shutil.copy(ROOT / name, self.dir / name)
        (self.dir / "data" / "daily").mkdir(parents=True)
        (self.dir / "data" / "weekly").mkdir(parents=True)
        self.cfg = load_config(self.dir)

    def put_daily(self, issue: dict) -> Path:
        p = self.dir / "data" / "daily" / f"{issue['date']}.json"
        p.write_text(json.dumps(issue, ensure_ascii=False, indent=2) + "\n", "utf-8")
        return p

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
