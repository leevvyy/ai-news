"""Read/write access to the on-disk archive (data/daily/*.json, data/weekly/*.json)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import cached_property
from pathlib import Path
from typing import Any, Iterator

from .config import Config

Issue = dict[str, Any]
Item = dict[str, Any]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


# ---- serialisation ---------------------------------------------------------
def load_json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def dump_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", "utf-8")


def write_text(path: Path, text: str) -> bool:
    """Write only when content changed; returns True if the file was (re)written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text("utf-8") == text:
        return False
    path.write_text(text, "utf-8")
    return True


# ---- time helpers ----------------------------------------------------------
def parse_ts(value: str) -> datetime:
    """ISO-8601 → aware UTC datetime. Accepts a trailing 'Z'."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return dt.astimezone(timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_bounds(wid: str) -> tuple[date, date]:
    m = _WEEK_RE.match(wid)
    if not m:
        raise ValueError(f"bad ISO week id {wid!r} (expected YYYY-Www)")
    monday = date.fromisocalendar(int(m[1]), int(m[2]), 1)
    return monday, monday + timedelta(days=6)


def daterange(start: date, end: date) -> Iterator[date]:
    """Inclusive on both ends."""
    for n in range((end - start).days + 1):
        yield start + timedelta(days=n)


# ---- archive ---------------------------------------------------------------
@dataclass
class Archive:
    cfg: Config
    _daily: dict[date, Issue] = field(default_factory=dict, repr=False)
    _weekly: dict[str, Issue] = field(default_factory=dict, repr=False)

    # dailies
    def daily_path(self, d: date) -> Path:
        return self.cfg.daily_data / f"{d.isoformat()}.json"

    @cached_property
    def _daily_dates(self) -> list[date]:
        if not self.cfg.daily_data.exists():
            return []
        return sorted(date.fromisoformat(p.stem) for p in self.cfg.daily_data.glob("*.json")
                      if _DATE_RE.match(p.stem))

    def daily_dates(self) -> list[date]:
        return list(self._daily_dates)

    def daily(self, d: date) -> Issue | None:
        if d not in self._daily:
            p = self.daily_path(d)
            if not p.exists():
                return None
            self._daily[d] = load_json(p)
        return self._daily[d]

    def put_daily(self, d: date, issue: Issue) -> None:
        """Replace the cached copy (used after scoring, before writing)."""
        self._daily[d] = issue
        if d not in self._daily_dates:
            self._daily_dates.append(d)
            self._daily_dates.sort()

    def latest_daily(self) -> date | None:
        return self._daily_dates[-1] if self._daily_dates else None

    def dailies_between(self, start: date, end: date) -> Iterator[tuple[date, Issue]]:
        for d in self._daily_dates:
            if start <= d <= end and (iss := self.daily(d)) is not None:
                yield d, iss

    # weeklies
    def weekly_path(self, wid: str) -> Path:
        return self.cfg.weekly_data / f"{wid}.json"

    def weekly_ids(self) -> list[str]:
        if not self.cfg.weekly_data.exists():
            return []
        return sorted(p.stem for p in self.cfg.weekly_data.glob("*.json") if _WEEK_RE.match(p.stem))

    def weekly(self, wid: str) -> Issue | None:
        if wid not in self._weekly:
            p = self.weekly_path(wid)
            if not p.exists():
                return None
            self._weekly[wid] = load_json(p)
        return self._weekly[wid]

    def put_weekly(self, wid: str, weekly: Issue) -> None:
        self._weekly[wid] = weekly

    # items across both stores
    def items_between(self, start: date, end: date) -> Iterator[tuple[date, Item]]:
        """Every archived item whose issue date (or, for weekly back-fill items,
        publication date in the reader's timezone) falls in [start, end]."""
        seen: set[str] = set()
        for d, iss in self.dailies_between(start, end):
            for it in iss.get("items", []):
                seen.add(it["id"])
                yield d, it
        for wid in self.weekly_ids():
            mon, sun = week_bounds(wid)
            if sun < start or mon > end:
                continue
            for it in (self.weekly(wid) or {}).get("items", []):
                if it["id"] in seen:
                    continue
                d = parse_ts(it["published_at"]).astimezone(self.cfg.tz).date()
                if start <= d <= end:
                    yield d, it
