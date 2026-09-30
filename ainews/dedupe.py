r"""Duplicate detection against the archive.

Two items are the same story when either

  * they share a canonical source URL (scheme/host case, `www.`, tracking params,
    fragment and trailing slash removed), or
  * their title token sets overlap, J(A, B) = |A ∩ B| / |A ∪ B| ≥ θ, AND they share
    at least one canonical entity.

A genuine development of an earlier story is allowed by setting
`"follow_up_of": "<earlier item id>"` on the new item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .archive import Archive, Item
from .config import Config

_TRACKING = re.compile(r"^(utm_|ref$|ref_|fbclid$|gclid$|mc_|igshid$|spm$|from$|share)", re.I)
_WORD = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*|[一-鿿]")
_STOP = frozenset("""
a an and are as at be by for from has have in into is it its of on or over says said the to
up with new its via vs after amid will than that this what how why who
""".split())


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k)))
    path = p.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query, ""))


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def title_tokens(title: str) -> frozenset[str]:
    return frozenset(t for t in _WORD.findall(title.lower()) if t not in _STOP)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


@dataclass(frozen=True, slots=True)
class Conflict:
    item_id: str
    other_id: str
    other_date: date | None      # None = same issue
    reason: str                  # "url" | "title" | "id"
    detail: str

    def __str__(self) -> str:
        where = "this issue" if self.other_date is None else self.other_date.isoformat()
        return f"{self.item_id!r} duplicates {self.other_id!r} ({where}) by {self.reason}: {self.detail}"


def _entities(cfg: Config, it: Item) -> set[str]:
    return {cfg.canonical_entity(e).casefold() for e in it.get("entities", [])}


def _compare(cfg: Config, it: Item, other: Item, other_date: date | None) -> Conflict | None:
    if it.get("follow_up_of") == other["id"]:
        return None
    mine = {canonical_url(s["url"]) for s in it["sources"]}
    theirs = {canonical_url(s["url"]) for s in other["sources"]}
    if shared := sorted(mine & theirs):
        return Conflict(it["id"], other["id"], other_date, "url", shared[0])
    j = jaccard(title_tokens(it["title"]), title_tokens(other["title"]))
    if j >= cfg.title_jaccard_threshold and _entities(cfg, it) & _entities(cfg, other):
        return Conflict(it["id"], other["id"], other_date, "title", f"J={j:.2f}")
    return None


def find_conflicts(cfg: Config, archive: Archive, items: list[Item], on: date) -> list[Conflict]:
    out: list[Conflict] = []
    seen_ids: set[str] = set()
    for i, it in enumerate(items):
        if it["id"] in seen_ids:
            out.append(Conflict(it["id"], it["id"], None, "id", "duplicate id"))
        seen_ids.add(it["id"])
        for other in items[:i]:
            if c := _compare(cfg, it, other, None):
                out.append(c)
    lo, hi = on - timedelta(days=cfg.dedupe_lookback_days), on - timedelta(days=1)
    prior = list(archive.items_between(lo, hi))
    for it in items:
        for d, other in prior:
            if c := _compare(cfg, it, other, d):
                out.append(c)
    return out
