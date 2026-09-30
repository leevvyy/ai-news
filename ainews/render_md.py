"""GitHub-readable Markdown for daily and weekly issues."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .aggregate import calendar_as_of, china_desk, lang_split, market_for, rolling_week, weekly_view
from .archive import Archive, Issue, parse_ts
from .config import COMPONENTS, REGIONS, TOPICS, Config

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“(])")


def esc(text: str) -> str:
    """Escape for a Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def first_sentence(text: str) -> str:
    return _SENT.split(" ".join(text.split()), maxsplit=1)[0]


def bar(frac: float, width: int = 20) -> str:
    """Unicode bar with eighth-block resolution, for Markdown charts."""
    eighths = round(max(0.0, min(1.0, frac)) * width * 8)
    full, rem = divmod(eighths, 8)
    return "█" * full + ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")[rem]


def bar_cell(frac: float, width: int) -> str:
    b = bar(frac, width)
    return f"`{b}` " if b else ""


def link(label: str, url: str) -> str:
    return f"[{label.replace('[', '(').replace(']', ')')}]({url})"


def _sources(it: dict) -> str:
    return " · ".join(f"{link(s['outlet'], s['url'])} <sup>{s['tier']}{' · 中文' if s['lang'] == 'zh' else ''}</sup>"
                      for s in it["sources"])


def _window_line(cfg: Config, iss: Issue) -> str:
    s, e = parse_ts(iss["window"]["start"]), parse_ts(iss["window"]["end"])
    ls, le = s.astimezone(cfg.tz), e.astimezone(cfg.tz)
    return (f"{s:%Y-%m-%d %H:%M} → {e:%Y-%m-%d %H:%M} UTC "
            f"({ls:%a %H:%M} → {le:%a %H:%M} {cfg.tz_label})")


def pct(x: float | None, signed: bool = True) -> str:
    return "–" if x is None else f"{100 * x:+.2f}%" if signed else f"{100 * x:.2f}%"


def _market_rows(cfg: Config, items: list[dict], events: dict[str, list[dict]]) -> list[str]:
    thr = float(cfg.market.get("t_threshold", 2.0))
    rows = ["| Story | Ticker | Day 0 | AR per session | CAR | t | β |", "|---|---|---|---|--:|--:|--:|"]
    for it in items:
        for ev in events.get(it["id"], []):
            if ev["status"] in ("ok", "partial"):
                ars = " · ".join(pct(a) for a in ev["ar"])
                t = f"{ev['t']:.2f}" if ev["t"] is not None else "–"
                t = f"**{t}**" if ev["t"] is not None and abs(ev["t"]) >= thr else t
                rows.append(f"| {esc(it['title'][:70])} | `{ev['symbol']}` | {ev['day0']} | {ars} | "
                            f"{pct(ev['car'])} | {t} | {ev['beta']:.2f} |")
            elif ev["status"] != "no-data":
                rows.append(f"| {esc(it['title'][:70])} | `{ev['symbol']}` | {ev['day0'] or '–'} | {ev['status']} | | | |")
    return rows if len(rows) > 2 else []


def _dash(cfg: Config) -> str:
    return f" · {link('Live dashboard', cfg.artifact_url)}" if cfg.artifact_url else ""


def _score_table(cfg: Config, it: dict) -> list[str]:
    sc = it["score"]
    rows = ["| component | $s_k$ | $w_k$ | $w_k s_k$ |", "|---|---:|---:|---:|"]
    for k in COMPONENTS:
        s, w = sc["components"][k], cfg.weights[k]
        rows.append(f"| {k} | {s:.3f} | {w:.2f} | {w * s:.3f} |")
    rows.append(f"| **I = 100·Σ** | | | **{sc['total']:.1f}** |")
    return rows


def render_daily(cfg: Config, archive: Archive, d: date) -> str:
    iss = archive.daily(d)
    assert iss is not None
    items = iss["items"]
    leads = [it for it in items if it["placement"] == "lead"]
    briefs = [it for it in items if it["placement"] == "brief"]
    noted = [it for it in items if it["placement"] == "noted"]
    langs = lang_split(items)
    n_src = sum(langs.values()) or 1
    out: list[str] = [
        f"# AI Daily · {d:%a %d %b %Y}",
        "",
        f"<sub>Window {_window_line(cfg, iss)} · {len(items)} stories · sources "
        f"{100 * langs['en'] / n_src:.0f}% EN / {100 * langs['zh'] / n_src:.0f}% 中文{_dash(cfg)}</sub>",
        "",
        "## TL;DR",
        "",
        *[f"- {t}" for t in iss["tldr"]],
        "",
        "## Lead stories",
        "",
    ]
    for n, it in enumerate(leads, 1):
        tickers = " · " + " ".join(f"`{t}`" for t in it["tickers"]) if it.get("tickers") else ""
        out += [
            f"### {n} · {it['title']}",
            "",
            f"`I = {it['score']['total']:.1f}` · {TOPICS[it['topic']]} · {REGIONS[it['region']]} · "
            f"{', '.join(it['entities'][:4])}{tickers}",
            "",
        ]
        if it.get("original_title"):
            out += [f"> 原标题：{it['original_title']}", ""]
        out += [it["summary"], "", f"**Why it matters.** {it['why_it_matters']}", ""]
        if it.get("key_numbers"):
            out += ["**Key numbers.** " + " · ".join(it["key_numbers"]), ""]
        if it.get("follow_up_of"):
            out += [f"*Follow-up to `{it['follow_up_of']}`.*", ""]
        if it.get("confidence_note"):
            out += [f"*Note: {it['confidence_note']}*", ""]
        out += [f"**Sources.** {_sources(it)}", "", "<details><summary>Score breakdown</summary>", "",
                *_score_table(cfg, it), "",
                f"coverage $c = {it['score']['coverage']}$ · entity baseline $\\mu_7 = {it['score']['baseline']:.2f}$"
                f" · verified {'✓' if it['score']['verified'] else '✗'}", "", "</details>", ""]

    if briefs:
        out += ["## Briefs", "", "| # | I | Topic | Story | Source |", "|--:|--:|---|---|---|"]
        for it in briefs:
            flag = " ⚠︎ *unverified*" if not it["score"]["verified"] else ""
            zh = " 〔中文〕" if it.get("original_title") else ""
            out.append(f"| {it['rank']} | {it['score']['total']:.1f} | {TOPICS[it['topic']]} | "
                       f"**{esc(it['title'])}**{zh}{flag}: {esc(first_sentence(it['summary']))} | "
                       f"{link(esc(it['sources'][0]['outlet']), it['sources'][0]['url'])} |")
        out.append("")

    desk = [it for it in china_desk(items) if it["placement"] != "lead"]
    if desk:
        out += ["## 中国 China desk", "",
                f"<sub>Chinese-market stories outside the leads · quota ≥ {cfg.china_quota} in leads + briefs</sub>", ""]
        for it in desk:
            tag = {"brief": "brief", "noted": "noted"}[it["placement"]] + (" · quota pick" if it.get("quota") else "")
            orig = f" · 原标题：{it['original_title']}" if it.get("original_title") else ""
            out.append(f"- {link(it['title'], it['sources'][0]['url'])} · I {it['score']['total']:.1f} · {tag}{orig}")
        out.append("")

    events = market_for(cfg, archive, leads + briefs)
    mrows = _market_rows(cfg, leads + briefs, events)
    if mrows:
        out += ["## Market read-through", "",
                "<sub>Market model: $AR_t = R_{i,t} - (\\hat\\alpha + \\hat\\beta R_{m,t})$, 60-session OLS ending 10 sessions "
                "before day 0 (first close after publication); CAR over sessions 0–2; t = CAR / (σ̂√n). "
                "Descriptive only; stories overlap and n is small.</sub>", "", *mrows, ""]

    cal = calendar_as_of(cfg, archive, d)
    if cal:
        out += ["## Upcoming", "", "| Date | Event | Kind | Status |", "|---|---|---|---|"]
        for ev in cal:
            title = link(esc(ev["title"]), ev["url"]) if ev.get("url") else esc(ev["title"])
            out.append(f"| {ev['date']} | {title} | {ev['kind']} | {ev['confidence']} |")
        out.append("")

    wk = rolling_week(cfg, archive, d)
    out += [f"## This week so far", "",
            f"<sub>Minor section · {wk['from']} → {wk['to']} · {wk['n']} stories over {wk['days']} archived day(s)</sub>",
            ""]
    if wk["top"]:
        out += ["**Also this week**", ""]
        out += [f"{i}. {link(r['title'], r['url'])} · I {r['score']:.1f} · {TOPICS[r['topic']]} · {r['date']}"
                for i, r in enumerate(wk["top"], 1)]
        out.append("")
    total = sum(wk["topics"].values()) or 1
    out += ["| Topic (7d) | Stories | Share |", "|---|--:|---|"]
    out += [f"| {TOPICS[k]} | {v} | {bar_cell(v / total, 16)}{100 * v / total:.0f}% |"
            for k, v in wk["topics"].items()]
    out.append("")
    if wk["entities"]:
        out += ["**Most-covered entities (Σ I, 7d):** " +
                " · ".join(f"{e['entity']} {e['weight']:.0f} ({e['mentions']})" for e in wk["entities"]), ""]

    if noted:
        out += ["## Also noted", ""]
        out += [f"- {link(it['title'], it['sources'][0]['url'])} · I {it['score']['total']:.1f}"
                f"{' · unverified' if not it['score']['verified'] else ''}" for it in noted]
        out.append("")

    if iss.get("notes"):
        out += ["## Editor's notes", "", iss["notes"], ""]

    out += ["---", "",
            "<sub>Ranking: $I = 100\\sum_k w_k s_k$ over impact, novelty, credibility, breadth, momentum; "
            "see [METHODOLOGY.md](../../METHODOLOGY.md). Generated by `python3 -m ainews build "
            f"{d.isoformat()}`; do not edit by hand.</sub>", ""]
    return "\n".join(out)


def render_weekly(cfg: Config, archive: Archive, wid: str) -> str:
    v: dict[str, Any] = weekly_view(cfg, archive, wid)
    mon, sun = date.fromisoformat(v["start"]), date.fromisoformat(v["end"])
    basis = (f"{v['n']} stories from {v['daily_issues']} daily issue(s)"
             + (" + back-filled research" if v["backfilled"] else ""))
    out = [f"# AI Weekly · {wid}", "", f"<sub>{mon:%a %d %b} → {sun:%a %d %b %Y} · {basis}{_dash(cfg)}</sub>", ""]
    if v["headline"]:
        out += [f"> {v['headline']}", ""]
    if v["themes"]:
        out += ["## Themes", ""]
        for th in v["themes"]:
            out += [f"### {th['title']}", "", th["summary"], ""]
            out += [f"- {link(r['title'], r['url'])} · I {r['score']:.1f}" for r in th["items"]]
            out.append("")
    out += ["## Top stories", "", "| # | I | Date | Topic | Story |", "|--:|--:|---|---|---|"]
    out += [f"| {i} | {r['score']:.1f} | {r['date'] or ''} | {TOPICS[r['topic']]} | {link(esc(r['title']), r['url'])} |"
            for i, r in enumerate(v["top"], 1)]
    out.append("")
    total = sum(v["topics"].values()) or 1
    out += ["## Topic mix", "", "| Topic | Stories | Share |", "|---|--:|---|"]
    out += [f"| {TOPICS[k]} | {n} | {bar_cell(n / total, 20)}{100 * n / total:.0f}% |" for k, n in v["topics"].items()]
    out.append("")
    if v["entities"]:
        out += ["## Entity leaderboard", "", "| Entity | Mentions | Σ I |", "|---|--:|--:|"]
        out += [f"| {esc(e['entity'])} | {e['mentions']} | {e['weight']:.1f} |" for e in v["entities"]]
        out.append("")
    mk = v["market"]
    if mk["by_topic"]:
        out += ["## Market read-through", "", f"<sub>{mk['n_events']} completed ticker events · mean CAR[0,2] with 95% t-interval</sub>", "",
                "| Topic | n | mean CAR | 95% CI |", "|---|--:|--:|---|"]
        for r in mk["by_topic"]:
            ci = f"[{pct(r['lo'])}, {pct(r['hi'])}]" if r["lo"] is not None else "–"
            out.append(f"| {TOPICS[r['topic']]} | {r['n']} | {pct(r['mean'])} | {ci} |")
        out.append("")
    out += ["## Stories per day", "", "| Day | " + " | ".join(TOPICS.values()) + " | Total |",
            "|---|" + "--:|" * (len(TOPICS) + 1)]
    for p in v["per_day"]:
        d = date.fromisoformat(p["date"])
        cells = " | ".join(str(p["counts"][k]) for k in TOPICS)
        out.append(f"| {d:%a %d} | {cells} | {sum(p['counts'].values())} |")
    out += ["", "---", "", f"<sub>Generated by `python3 -m ainews weekly {wid}`; do not edit by hand.</sub>", ""]
    return "\n".join(out)
