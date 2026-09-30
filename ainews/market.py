r"""Market read-through: a tidy price store and a market-model event study.

For a story published at time τ that names ticker i (benchmark m = its home index):

    day 0       = first session of i's exchange whose close is after τ
    R_t         = P_t / P_{t-1} − 1                     (adjusted closes, common sessions of i and m)
    α̂, β̂       = OLS of R_i on R_m over sessions [−(L+G), −G)      L = 60, G = 10
    AR_t        = R_i,t − (α̂ + β̂ · R_m,t)              t = 0 … K−1,  K = 3
    CAR         = Σ_t AR_t
    t-stat      = CAR / (σ̂ · √n),   σ̂ = residual sd in the estimation window (ddof = 2)

Stories overlap and n is small, so this is descriptive, never a trading signal.

Prices live in data/market/prices.csv (symbol,date,close). The nightly harvester merges new
rows; when a corporate action restates history, the stored series is rescaled by the median
overlap ratio, which leaves every return unchanged.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics as st
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

Series = dict[date, float]
Prices = dict[str, Series]

UA = "Mozilla/5.0 (compatible; ai-daily-harvester/1.0; +https://github.com/leevvyy/ai-news)"

# Exchange of a symbol, keyed by suffix: (IANA timezone, regular-session close).
EXCHANGES: Mapping[str, tuple[str, time]] = {
    "": ("America/New_York", time(16, 0)),
    ".HK": ("Asia/Hong_Kong", time(16, 0)),
    ".KS": ("Asia/Seoul", time(15, 30)),
    ".KQ": ("Asia/Seoul", time(15, 30)),
    ".T": ("Asia/Tokyo", time(15, 30)),
    ".TW": ("Asia/Taipei", time(13, 30)),
    ".SS": ("Asia/Shanghai", time(15, 0)),
    ".SZ": ("Asia/Shanghai", time(15, 0)),
}
INDEX_SUFFIX = {"^GSPC": "", "^SOX": "", "^IXIC": "", "^HSI": ".HK", "^KS11": ".KS", "^N225": ".T", "^TWII": ".TW"}
_SUFFIX = re.compile(r"(\.[A-Z]{1,2})$")

# Two-sided 97.5% Student-t quantiles for small samples (df → t); df > 30 uses 1.96.
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
         10: 2.228, 12: 2.179, 15: 2.131, 20: 2.086, 25: 2.060, 30: 2.042}


def suffix(symbol: str) -> str:
    if symbol in INDEX_SUFFIX:
        return INDEX_SUFFIX[symbol]
    m = _SUFFIX.search(symbol)
    return m[1] if m and m[1] in EXCHANGES else ""


def benchmark_for(market_cfg: Mapping, symbol: str) -> str:
    return dict(market_cfg.get("benchmarks", {})).get(suffix(symbol), "^GSPC")


def t_crit(df: int) -> float:
    if df > 30:
        return 1.96
    return _T975[min(k for k in _T975 if k >= df)] if df >= 1 else float("nan")


# ---- store -------------------------------------------------------------------
def load_prices(path: Path) -> Prices:
    out: Prices = {}
    if not path.exists():
        return out
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out.setdefault(row["symbol"], {})[date.fromisoformat(row["date"])] = float(row["close"])
    return out


def dump_prices(prices: Prices) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["symbol", "date", "close"])
    for sym in sorted(prices):
        for d in sorted(prices[sym]):
            w.writerow([sym, d.isoformat(), f"{prices[sym][d]:.6g}"])
    return buf.getvalue()


def merge_series(stored: Series, fetched: Series, refresh_last: int = 5) -> Series:
    """Upsert fetched closes; rescale history if the provider restated it (splits, dividends)."""
    if not fetched:
        return dict(stored)
    overlap = sorted(set(stored) & set(fetched))[-20:]
    base = dict(stored)
    if overlap:
        ratios = sorted(stored[d] / fetched[d] for d in overlap if fetched[d])
        med = ratios[len(ratios) // 2] if ratios else 1.0
        if abs(med - 1.0) > 0.005:
            base = {d: v / med for d, v in stored.items()}
    fresh = set(sorted(fetched)[-refresh_last:])
    for d, v in fetched.items():
        if d not in base or d in fresh:
            base[d] = v
    return base


def fetch_yahoo(symbol: str, start: date, end: date, timeout: float = 20.0) -> Series:
    """Daily adjusted closes from Yahoo's chart API (network; used by the harvester only)."""
    p1 = int(datetime.combine(start, time(), timezone.utc).timestamp())
    p2 = int(datetime.combine(end + timedelta(days=1), time(), timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?period1={p1}&period2={p2}&interval=1d&events=div%2Csplits")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        res = json.load(resp)["chart"]["result"][0]
    tz = ZoneInfo(res["meta"].get("exchangeTimezoneName") or "UTC")
    ind = res["indicators"]
    closes = (ind.get("adjclose") or [{}])[0].get("adjclose") or ind["quote"][0]["close"]
    out: Series = {}
    for ts, c in zip(res.get("timestamp") or [], closes):
        if c is not None and math.isfinite(c):
            out[datetime.fromtimestamp(ts, tz).date()] = float(c)
    return out


# ---- event study ---------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Event:
    symbol: str
    bench: str
    status: str                   # ok | partial | pending | no-data | short-history
    day0: str | None = None
    ar: tuple[float, ...] = ()
    car: float | None = None
    t: float | None = None
    beta: float | None = None
    n: int = 0

    def as_json(self) -> dict:
        d = asdict(self)
        d["ar"] = [round(x, 5) for x in self.ar]
        for k in ("car", "t", "beta"):
            if d[k] is not None:
                d[k] = round(d[k], 4 if k != "car" else 5)
        return d


def returns(series: Series) -> Series:
    ds = sorted(series)
    return {b: series[b] / series[a] - 1.0 for a, b in zip(ds, ds[1:]) if series[a]}


def ols(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """(alpha, beta, residual sd with ddof=2)."""
    mx, my = st.fmean(x), st.fmean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx if sxx else 0.0
    alpha = my - beta * mx
    rss = sum((b - alpha - beta * a) ** 2 for a, b in zip(x, y))
    return alpha, beta, math.sqrt(rss / (len(x) - 2)) if len(x) > 2 else float("nan")


def event_study(market_cfg: Mapping, prices: Prices, symbol: str, published: datetime) -> Event:
    bench = benchmark_for(market_cfg, symbol)
    L, G = int(market_cfg.get("estimation_days", 60)), int(market_cfg.get("estimation_gap", 10))
    K = int(market_cfg.get("car_days", 3))
    s, b = prices.get(symbol), prices.get(bench)
    if not s or not b:
        return Event(symbol, bench, "no-data")
    tzname, close_t = EXCHANGES[suffix(symbol)]
    tz = ZoneInfo(tzname)
    sessions = sorted(s)
    day0 = next((d for d in sessions if datetime.combine(d, close_t, tz) > published), None)
    if day0 is None:
        return Event(symbol, bench, "pending")
    rs, rb = returns(s), returns(b)
    common = [d for d in sessions if d in rs and d in rb]
    k = next((i for i, d in enumerate(common) if d >= day0), None)
    if k is None:
        return Event(symbol, bench, "pending", day0.isoformat())
    est = common[max(0, k - G - L): max(0, k - G)]
    if len(est) < int(0.8 * L):
        return Event(symbol, bench, "short-history", common[k].isoformat())
    alpha, beta, sigma = ols([rb[d] for d in est], [rs[d] for d in est])
    window = common[k:k + K]
    ar = tuple(rs[d] - (alpha + beta * rb[d]) for d in window)
    car = sum(ar)
    t = car / (sigma * math.sqrt(len(ar))) if sigma and math.isfinite(sigma) else None
    return Event(symbol, bench, "ok" if len(ar) == K else "partial", common[k].isoformat(),
                 ar, car, t, beta, len(ar))


def item_events(market_cfg: Mapping, prices: Prices, item: dict, max_tickers: int = 3) -> list[dict]:
    if not item.get("tickers"):
        return []
    published = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
    return [event_study(market_cfg, prices, sym, published).as_json() for sym in item["tickers"][:max_tickers]]


def mean_ci(values: Iterable[float]) -> dict | None:
    xs = list(values)
    if not xs:
        return None
    m = st.fmean(xs)
    if len(xs) < 2:
        return {"n": 1, "mean": round(m, 5), "lo": None, "hi": None}
    half = t_crit(len(xs) - 1) * st.stdev(xs) / math.sqrt(len(xs))
    return {"n": len(xs), "mean": round(m, 5), "lo": round(m - half, 5), "hi": round(m + half, 5)}
