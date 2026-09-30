"""Configuration and controlled vocabularies.

`config.toml` holds the tunable numbers; the vocabularies below are code because
the renderers depend on their order (topic order = colour slot order).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent

# Fixed order: index i is categorical colour slot i+1 in the dashboard.
TOPICS: Mapping[str, str] = MappingProxyType({
    "models": "Models & labs",
    "research": "Research",
    "products": "Products & agents",
    "business": "Business & funding",
    "compute": "Compute & chips",
    "policy": "Policy & safety",
})
REGIONS: Mapping[str, str] = MappingProxyType({
    "us": "US", "cn": "China", "eu": "Europe", "global": "Global", "other": "Other",
})
LANGS: Mapping[str, str] = MappingProxyType({"en": "English", "zh": "中文", "other": "Other"})
TIERS: tuple[str, ...] = ("primary", "press", "trade", "social")
COMPONENTS: tuple[str, ...] = ("impact", "novelty", "credibility", "breadth", "momentum")
UPCOMING_KINDS: tuple[str, ...] = ("launch", "conference", "earnings", "policy", "deadline", "result", "other")
CONFIDENCE: tuple[str, ...] = ("confirmed", "expected", "rumored")
PLACEMENTS: tuple[str, ...] = ("lead", "brief", "noted")


@dataclass(frozen=True, slots=True)
class KnownDomain:
    name: str
    tier: str
    lang: str
    group: str


@dataclass(frozen=True, slots=True)
class Config:
    root: Path
    utc_offset_hours: int
    run_local_time: str
    max_window_hours: int
    grace_hours: int
    leads: int
    max_leads_per_entity: int
    briefs: int
    china_quota: int
    dedupe_lookback_days: int
    title_jaccard_threshold: float
    dashboard_days: int
    calendar_horizon_days: int
    breadth_kappa: float
    momentum_baseline_days: int
    credibility_top_n: int
    weights: Mapping[str, float]
    tier_reliability: Mapping[str, float]
    min_independent: int
    aliases: Mapping[str, str]
    artifact_url: str
    repo_url: str
    publish_enabled: bool
    site_url: str
    publish_target: str
    site_title: str
    known_domains: Mapping[str, KnownDomain]
    market: Mapping[str, Any]
    harvest: Mapping[str, Any]

    # ---- derived -------------------------------------------------------
    @property
    def tz(self) -> tzinfo:
        return timezone(timedelta(hours=self.utc_offset_hours))

    @property
    def tz_label(self) -> str:
        return f"UTC{self.utc_offset_hours:+d}"

    @property
    def raw_data(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def prices_path(self) -> Path:
        return self.root / "data" / "market" / "prices.csv"

    @property
    def daily_data(self) -> Path:
        return self.root / "data" / "daily"

    @property
    def weekly_data(self) -> Path:
        return self.root / "data" / "weekly"

    @property
    def daily_md(self) -> Path:
        return self.root / "issues" / "daily"

    @property
    def weekly_md(self) -> Path:
        return self.root / "issues" / "weekly"

    @property
    def dashboard_path(self) -> Path:
        return self.root / "artifacts" / "latest.html"

    def canonical_entity(self, name: str) -> str:
        name = name.strip()
        return self.aliases.get(name.casefold(), name)

    def known_domain(self, url: str) -> KnownDomain | None:
        """Longest-suffix match of the URL host against sources.toml domains."""
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        parts = host.split(".")
        for i in range(len(parts) - 1):
            if (hit := self.known_domains.get(".".join(parts[i:]))) is not None:
                return hit
        return None


def _load_domains(path: Path) -> dict[str, KnownDomain]:
    if not path.exists():
        return {}
    table = tomllib.loads(path.read_text("utf-8"))
    out: dict[str, KnownDomain] = {}
    for src in table.get("source", []):
        kd = KnownDomain(src["name"], src["tier"], src.get("lang", "other"), src.get("group", ""))
        for dom in src["domains"]:
            out[dom.removeprefix("www.")] = kd
    return out


def load_config(root: Path | str = ROOT) -> Config:
    root = Path(root)
    raw = tomllib.loads((root / "config.toml").read_text("utf-8"))
    sch, iss, sc = raw["schedule"], raw["issue"], raw["score"]
    weights = {k: float(sc["weights"][k]) for k in COMPONENTS}
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"score.weights must sum to 1, got {sum(weights.values()):.6f}")
    tiers = {k: float(sc["tiers"][k]) for k in TIERS}
    if not all(0.0 <= r < 1.0 for r in tiers.values()):
        raise ValueError("score.tiers reliabilities must lie in [0, 1)")
    aliases = {k.casefold(): v for k, v in raw.get("entities", {}).get("aliases", {}).items()}
    pub = raw.get("publish", {})
    art = raw.get("artifact", {})
    return Config(
        root=root,
        utc_offset_hours=int(sch["utc_offset_hours"]),
        run_local_time=str(sch["run_local_time"]),
        max_window_hours=int(sch["max_window_hours"]),
        grace_hours=int(sch["grace_hours"]),
        leads=int(iss["leads"]),
        max_leads_per_entity=int(iss.get("max_leads_per_entity", 2)),
        china_quota=int(iss.get("china_quota", 0)),
        briefs=int(iss["briefs"]),
        dedupe_lookback_days=int(iss["dedupe_lookback_days"]),
        title_jaccard_threshold=float(iss["title_jaccard_threshold"]),
        dashboard_days=int(iss["dashboard_days"]),
        calendar_horizon_days=int(iss["calendar_horizon_days"]),
        breadth_kappa=float(sc["breadth_kappa"]),
        momentum_baseline_days=int(sc["momentum_baseline_days"]),
        credibility_top_n=int(sc.get("credibility_top_n", 3)),
        weights=MappingProxyType(weights),
        tier_reliability=MappingProxyType(tiers),
        min_independent=int(sc.get("verification", {}).get("min_independent", 2)),
        aliases=MappingProxyType(aliases),
        artifact_url=str(art.get("url", "")),
        repo_url=str(art.get("repo_url", "")).rstrip("/"),
        publish_enabled=bool(pub.get("enabled", False)),
        site_url=str(pub.get("site_url", "")),
        publish_target=str(pub.get("target", "github-pages")),
        site_title=str(pub.get("title", "AI Daily")),
        known_domains=MappingProxyType(_load_domains(root / "sources.toml")),
        market=MappingProxyType(raw.get("market", {})),
        harvest=MappingProxyType(raw.get("harvest", {})),
    )
