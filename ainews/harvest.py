r"""Nightly harvester: feeds + APIs → data/raw/<date>.json, prices → data/market/prices.csv.

Runs in GitHub Actions (.github/workflows/harvest.yml), because the routine's sandbox cannot
reach news hosts. The routine then reads the raw file with `python3 -m ainews raw`.

Story clusters: headlines are joined when their token sets overlap,

    J(A, B) = |A ∩ B| / |A ∪ B| ≥ θ   (θ = 0.4; English words, CJK character bigrams)

using single-link union-find. A cluster's n_outlets (distinct outlets) is an objective
breadth signal for the `coverage` field.
"""

from __future__ import annotations

import hashlib
import html
import html.entities
import json
import re
import time as _time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Callable

from . import market
from .archive import Archive, dump_json, fmt_ts, load_json
from .config import Config
from .dedupe import canonical_url

UA = market.UA
AI_PATTERN = re.compile(
    r"\b(AI|A\.I\.|AGI|artificial intelligence|machine learning|LLMs?|GPT[-\w.]*|chatbots?|OpenAI|Anthropic|Claude|"
    r"Gemini|DeepMind|Llama|Mistral|Grok|xAI|Copilot|Nvidia|GPUs?|HBM|TPU|semiconductors?|chips?|data ?cent(?:er|re)s?|"
    r"DeepSeek|Qwen|Kimi|Moonshot|Zhipu|MiniMax|agents?|agentic|robot\w*|neural|transformer|inference|"
    r"foundation models?|diffusion|multimodal)\b|"
    r"人工智能|大模型|智能体|算力|芯片|机器人|模型|具身|英伟达|昇腾|通义|千问|豆包|智谱|月之暗面|深度求索|DeepSeek|AI",
    re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")
_CJK = re.compile(r"[一-鿿]+")
_STOP = frozenset("a an and are as at be by for from has have in into is it its of on or over says the to up with new "
                  "after amid will than that this what how why who report reports".split())
NS = {"atom": "http://www.w3.org/2005/Atom", "rss1": "http://purl.org/rss/1.0/",
      "dc": "http://purl.org/dc/elements/1.1/", "content": "http://purl.org/rss/1.0/modules/content/"}


# ---- helpers -------------------------------------------------------------------
def clean_text(raw: str | None, limit: int = 600) -> str:
    text = _WS.sub(" ", html.unescape(_TAG.sub(" ", raw or ""))).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def cluster_tokens(title: str) -> frozenset[str]:
    low = title.lower()
    words = {w for w in _WORD.findall(low) if w not in _STOP and len(w) > 1}
    bigrams = {run[i:i + 2] for run in _CJK.findall(title) for i in range(len(run) - 1)}
    return frozenset(words | bigrams)


def is_ai(text: str) -> bool:
    return bool(AI_PATTERN.search(text))


def fetch(url: str, timeout: float = 30.0, retries: int = 1) -> bytes:
    """GET with one retry on timeouts, 429 and 5xx (honouring Retry-After up to 10 s).
    4xx other than 429 is a site decision (gone, forbidden to bots) and is not retried."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if attempt == retries or not (exc.code == 429 or exc.code >= 500):
                raise
            wait = exc.headers.get("Retry-After", "") if exc.headers else ""
            _time.sleep(min(10.0, float(wait)) if wait.isdigit() else 3.0)
        except (TimeoutError, urllib.error.URLError):
            if attempt == retries:
                raise
            _time.sleep(3.0)
    raise RuntimeError("unreachable")


_BAD_XML = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ENTITY = re.compile(rb"&(#[0-9]{1,7};|#x[0-9A-Fa-f]{1,6};|[A-Za-z][A-Za-z0-9]{0,31};)?")
_XML_ENTITIES = {b"amp;", b"lt;", b"gt;", b"quot;", b"apos;"}


def sanitize_xml(data: bytes) -> bytes:
    """Repair the usual feed breakage: control characters, bare '&', HTML-only entities (&nbsp;)."""
    def fix(m: re.Match) -> bytes:
        ent = m.group(1)
        if ent is None:
            return b"&amp;"
        if ent.startswith(b"#") or ent in _XML_ENTITIES:
            return m.group(0)
        cp = html.entities.name2codepoint.get(ent[:-1].decode("ascii"))
        return f"&#{cp};".encode() if cp else b"&amp;" + ent
    return _ENTITY.sub(fix, _BAD_XML.sub(b"", data))


# ---- parsers -------------------------------------------------------------------
def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_feed(data: bytes) -> list[dict]:
    """RSS 2.0, RSS 1.0 (RDF) and Atom → [{title, url, published, summary, outlet?, outlet_url?}]."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        root = ET.fromstring(sanitize_xml(data))
    out: list[dict] = []
    for it in root.iter("item"):                                     # RSS 2.0
        src = it.find("source")
        out.append({"title": _text(it.find("title")), "url": _text(it.find("link")),
                    "published": _text(it.find("pubDate")) or _text(it.find("dc:date", NS)),
                    "summary": _text(it.find("content:encoded", NS)) or _text(it.find("description")),
                    "outlet": _text(src) or None, "outlet_url": src.get("url") if src is not None else None})
    for it in root.iter(f"{{{NS['rss1']}}}item"):                    # RSS 1.0
        out.append({"title": _text(it.find("rss1:title", NS)), "url": _text(it.find("rss1:link", NS)),
                    "published": _text(it.find("dc:date", NS)),
                    "summary": _text(it.find("rss1:description", NS))})
    for it in root.iter(f"{{{NS['atom']}}}entry"):                   # Atom
        link = next((l.get("href") for l in it.findall("atom:link", NS) if l.get("rel") in (None, "alternate")), "")
        out.append({"title": _text(it.find("atom:title", NS)), "url": link or "",
                    "published": _text(it.find("atom:published", NS)) or _text(it.find("atom:updated", NS)),
                    "summary": _text(it.find("atom:summary", NS)) or _text(it.find("atom:content", NS))})
    return [o for o in out if o["title"] and o["url"]]


def parse_hf(data: bytes) -> list[dict]:
    out = []
    for row in json.loads(data):
        p = row.get("paper", row)
        pid = p.get("id")
        if not pid:
            continue
        out.append({"title": p.get("title") or row.get("title", ""), "url": f"https://arxiv.org/abs/{pid}",
                    "published": row.get("publishedAt") or p.get("publishedAt"),
                    "summary": p.get("summary", ""), "outlet": "Hugging Face Daily Papers",
                    "signals": {"hf_upvotes": int(p.get("upvotes") or 0)}})
    return out


def parse_hn(data: bytes) -> list[dict]:
    out = []
    for h in json.loads(data).get("hits", []):
        hn_url = f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append({"title": h.get("title") or "", "url": h.get("url") or hn_url,
                    "published": datetime.fromtimestamp(h["created_at_i"], timezone.utc).isoformat(),
                    "summary": "", "outlet": "Hacker News",
                    "signals": {"hn_points": int(h.get("points") or 0),
                                "hn_comments": int(h.get("num_comments") or 0), "hn_url": hn_url}})
    return out


def feed_url(feed: dict, since: datetime, now: datetime) -> list[str]:
    kind = feed.get("kind", "rss")
    if kind == "gnews":
        loc = {"en": "hl=en-US&gl=US&ceid=US:en", "zh": "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"}[feed.get("locale", "en")]
        q = urllib.parse.quote(f"{feed['query']} when:2d")
        return [f"https://news.google.com/rss/search?q={q}&{loc}"]
    if kind == "hf":
        days = {now.date(), (now - timedelta(days=1)).date()}
        return [f"{feed['url']}?date={d.isoformat()}" for d in sorted(days)]
    if kind == "hn":
        nf = urllib.parse.quote(f"created_at_i>{int(since.timestamp())},points>={feed.get('min_points', 60)}")
        return [f"{feed['url']}?tags=story&numericFilters={nf}&hitsPerPage=300"]
    return [feed["url"]]


PARSERS: dict[str, Callable[[bytes], list[dict]]] = {"rss": parse_feed, "gnews": parse_feed, "hf": parse_hf, "hn": parse_hn}


# ---- clustering ------------------------------------------------------------------
def cluster(items: list[dict], threshold: float) -> list[dict]:
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    toks = [cluster_tokens(it["title"]) for it in items]
    for i, j in combinations(range(len(items)), 2):
        a, b = toks[i], toks[j]
        if a and b and len(a & b) / len(a | b) >= threshold:
            parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        its = sorted((items[i] for i in members), key=lambda it: (it["published_at"], it["id"]))
        outlets = sorted({it["outlet"] for it in its})
        # best link: most reliable tier, then the outlet's own URL over a Google News redirect, then earliest
        best = min(its, key=lambda it: ({"primary": 0, "press": 1, "trade": 2, "social": 3}[it["tier"]],
                                        it["via"] == "gnews", it["published_at"]))
        out.append({"title": best["title"], "url": best["url"], "lang": best["lang"],
                    "n_outlets": len(outlets), "outlets": outlets, "item_ids": [it["id"] for it in its],
                    "first_seen": its[0]["published_at"],
                    "hn_points": max((it.get("signals", {}).get("hn_points", 0) for it in its), default=0),
                    "hf_upvotes": max((it.get("signals", {}).get("hf_upvotes", 0) for it in its), default=0)})
    out.sort(key=lambda c: (-c["n_outlets"], -c["hn_points"], c["first_seen"], c["title"]))
    return out


# ---- harvest ---------------------------------------------------------------------
@dataclass
class FeedResult:
    name: str
    ok: bool = False
    n: int = 0
    error: str = ""
    items: list[dict] = field(default_factory=list)


def load_feeds(cfg: Config) -> list[dict]:
    feeds = tomllib.loads((cfg.root / "feeds.toml").read_text("utf-8")).get("feed", [])
    return [f for f in feeds if f.get("enabled", True)]


def _run_feed(cfg: Config, feed: dict, since: datetime, now: datetime,
              fetcher: Callable[[str], bytes]) -> FeedResult:
    res = FeedResult(feed["name"])
    parse = PARSERS[feed.get("kind", "rss")]
    limit = int(cfg.harvest.get("summary_chars", 600))
    urls = feed_url(feed, since, now)
    rows: list[dict] = []
    errors: list[str] = []
    for url in urls:  # a feed is ok if any of its URLs works (HF: today's page 400s until papers are posted)
        try:
            rows += parse(fetcher(url))
        except Exception as exc:  # network, HTTP, XML/JSON errors: record and move on
            errors.append(f"{type(exc).__name__}: {exc}")
    if len(errors) == len(urls):
        res.error = errors[-1][:200]
        return res
    res.ok = True
    for row in rows:
        published = parse_date(row.get("published"))
        if published is None or not since <= published <= now + timedelta(hours=1):
            continue
        title, summary = clean_text(row["title"], 300), clean_text(row.get("summary"), limit)
        if feed.get("kind") == "gnews" and row.get("outlet") and title.endswith(" - " + row["outlet"]):
            title = title[: -len(row["outlet"]) - 3]
        if feed.get("filter") and not is_ai(f"{title} {summary}"):
            continue
        outlet = row.get("outlet") or feed["name"]
        tier = feed.get("tier", "press")
        if feed.get("kind") == "gnews" and row.get("outlet_url"):
            known = cfg.known_domain(row["outlet_url"])
            tier = known.tier if known else "press"
        url = row["url"].strip()
        res.items.append({"id": hashlib.sha1(canonical_url(url).encode()).hexdigest()[:12],
                          "title": title, "url": url, "outlet": outlet, "feed": feed["name"],
                          "tier": tier, "lang": feed.get("lang", "en"), "published_at": fmt_ts(published),
                          "summary": summary, "via": feed.get("kind", "rss"),
                          **({"signals": row["signals"]} if row.get("signals") else {})})
    res.n = len(res.items)
    return res


def harvest_news(cfg: Config, now: datetime, fetcher: Callable[[str], bytes] = fetch) -> dict[str, Any]:
    since = now - timedelta(hours=int(cfg.harvest.get("since_hours", 36)))
    feeds = load_feeds(cfg)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda f: _run_feed(cfg, f, since, now, fetcher), feeds))
    merged: dict[str, dict] = {}
    for res in results:
        for it in res.items:
            prev = merged.get(it["id"])
            if prev is None or it["published_at"] < prev["published_at"]:
                if prev and prev.get("signals"):
                    it.setdefault("signals", {}).update(prev["signals"])
                merged[it["id"]] = it
            elif it.get("signals"):
                prev.setdefault("signals", {}).update(it["signals"])
    items = sorted(merged.values(), key=lambda it: (it["published_at"], it["id"]), reverse=True)
    return {
        "harvested_at": fmt_ts(now), "since": fmt_ts(since),
        "feeds": [{"name": r.name, "ok": r.ok, "n": r.n, **({"error": r.error} if r.error else {})} for r in results],
        "n_items": len(items),
        "clusters": cluster(items, float(cfg.harvest.get("cluster_jaccard", 0.4))),
        "items": items,
    }


def tracked_symbols(cfg: Config, archive: Archive) -> list[str]:
    syms = set(cfg.market.get("watchlist", [])) | set(dict(cfg.market.get("benchmarks", {})).values())
    for d in archive.daily_dates():
        syms |= {t for it in (archive.daily(d) or {}).get("items", []) for t in it.get("tickers", [])}
    for wid in archive.weekly_ids():
        syms |= {t for it in (archive.weekly(wid) or {}).get("items", []) for t in it.get("tickers", [])}
    syms |= {market.benchmark_for(cfg.market, s) for s in list(syms)}
    return sorted(syms)


def harvest_prices(cfg: Config, archive: Archive, today: date,
                   fetcher: Callable[[str, date, date], market.Series] = market.fetch_yahoo) -> list[dict]:
    prices = market.load_prices(cfg.prices_path)
    status = []
    for sym in tracked_symbols(cfg, archive):
        stored = prices.get(sym, {})
        start = (max(stored) - timedelta(days=14)) if stored else today - timedelta(days=int(cfg.market.get("history_days", 400)))
        try:
            fetched = fetcher(sym, start, today)
        except Exception as exc:
            status.append({"symbol": sym, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:160]})
            continue
        prices[sym] = market.merge_series(stored, fetched)
        status.append({"symbol": sym, "ok": True, "n_new": len(set(fetched) - set(stored))})
    cfg.prices_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.prices_path.write_text(market.dump_prices(prices), "utf-8")
    return status


def prune_raw(cfg: Config, today: date) -> list[Path]:
    keep = int(cfg.harvest.get("retention_days", 30))
    gone = []
    for p in sorted(cfg.raw_data.glob("*.json")):
        try:
            if (today - date.fromisoformat(p.stem)).days > keep:
                p.unlink()
                gone.append(p)
        except ValueError:
            continue
    return gone


def latest_raw(cfg: Config) -> Path | None:
    files = sorted(cfg.raw_data.glob("*.json")) if cfg.raw_data.exists() else []
    return files[-1] if files else None


def staleness(raw: dict, now: datetime) -> float:
    """Hours between the harvest and `now`."""
    return (now - datetime.fromisoformat(raw["harvested_at"].replace("Z", "+00:00"))).total_seconds() / 3600


def digest(raw: dict, top: int = 40, now: datetime | None = None) -> str:
    """Compact text view for the routine: clusters first, then primary items, HN, papers."""
    lines = [f"harvested {raw['harvested_at']} · since {raw['since']} · {raw['n_items']} items · "
             f"feeds ok {sum(f['ok'] for f in raw['feeds'])}/{len(raw['feeds'])}"]
    if now is not None and (age := staleness(raw, now)) > 6:
        lines.append(f"STALE: this harvest is {age:.0f} h old, so tonight's scheduled run did not land. Stories after "
                     f"{raw['harvested_at']} are missing here; cover the rest of the window with WebSearch and say so in notes.")
    bad = [f"{f['name']} ({f.get('error', 'no items')[:60]})" for f in raw["feeds"] if not f["ok"]]
    if bad:
        lines.append("failed feeds: " + "; ".join(bad))
    for lang, label in (("en", "EN"), ("zh", "中文")):
        cl = [c for c in raw["clusters"] if c["lang"] == lang][:top if lang == "en" else top // 2]
        lines.append(f"\n## {label} story clusters (n_outlets · first seen · title · best URL)")
        for c in cl:
            extra = f" · HN {c['hn_points']}" if c["hn_points"] else ""
            lines.append(f"- [{c['n_outlets']}] {c['first_seen']} · {c['title']} · {c['url']}{extra}")
            if c["n_outlets"] > 1:
                lines.append("    outlets: " + ", ".join(c["outlets"][:8]))
    prim = [it for it in raw["items"] if it["tier"] == "primary" and it["via"] != "hf"][:25]
    lines.append("\n## Primary sources")
    lines += [f"- {it['published_at']} · {it['outlet']} · {it['title']} · {it['url']}" for it in prim]
    papers = sorted((it for it in raw["items"] if it["via"] == "hf"),
                    key=lambda it: -it.get("signals", {}).get("hf_upvotes", 0))[:12]
    lines.append("\n## Papers (HF upvotes)")
    lines += [f"- [{it['signals']['hf_upvotes']}] {it['title']} · {it['url']}" for it in papers]
    hn = sorted((it for it in raw["items"] if it["via"] == "hn"),
                key=lambda it: -it.get("signals", {}).get("hn_points", 0))[:12]
    lines.append("\n## Hacker News (points · comments)")
    lines += [f"- [{it['signals']['hn_points']} · {it['signals']['hn_comments']}] {it['title']} · {it['url']}" for it in hn]
    return "\n".join(lines)


def write_raw(cfg: Config, raw: dict, now: datetime) -> Path:
    path = cfg.raw_data / f"{now.astimezone(cfg.tz).date().isoformat()}.json"
    dump_json(raw, path)
    return path


def load_raw(path: Path) -> dict:
    return load_json(path)
