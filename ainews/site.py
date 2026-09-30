"""Static site + Atom feed, built from the archive (publish-ready; deploy is gated in CI).

site/
  index.html                 living dashboard (standalone document)
  daily/<date>.html          one static page per issue (no JS)
  weekly/<week>.html         one static page per weekly recap
  archive.html               every issue, newest first
  feed.xml                   Atom 1.0: one entry per daily + weekly issue
"""

from __future__ import annotations

import html
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xesc

from . import dashboard
from .aggregate import calendar_as_of, weekly_view
from .archive import Archive, parse_ts, week_bounds, write_text
from .config import REGIONS, TOPICS, Config

_CSS = """
:root{--bg:#f3f4f7;--surface:#fff;--ink:#13141b;--ink-2:#434655;--muted:#676b7c;--line:#dadde6;--accent:#4a3aa7}
@media (prefers-color-scheme:dark){:root{--bg:#0d0e13;--surface:#16171e;--ink:#eceef5;--ink-2:#c1c4d1;--muted:#9095a6;--line:#2b2e3a;--accent:#9d93ee;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 "Source Serif 4",Georgia,serif}
main{max-width:760px;margin:0 auto;padding:32px 16px 64px}h1,h2,h3{font-family:"Bricolage Grotesque",system-ui,sans-serif;line-height:1.2;text-wrap:balance}
a{color:inherit;text-decoration-color:var(--accent)}.meta,.src{font:13px/1.5 ui-monospace,Menlo,monospace;color:var(--muted)}
article{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:16px 0}
.i{font:600 13px ui-monospace,Menlo,monospace;color:var(--accent)}table{border-collapse:collapse;width:100%;font-size:15px}
td,th{border-bottom:1px solid var(--line);padding:6px 8px 6px 0;text-align:left;vertical-align:top}nav{font:14px system-ui,sans-serif;margin-bottom:24px}
"""


def _page(title: str, body: str, *, root: str) -> str:
    return (f"<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title)}</title>"
            f"<link rel=\"alternate\" type=\"application/atom+xml\" href=\"{root}feed.xml\" title=\"AI Daily\">"
            f"<style>{_CSS}</style></head><body><main>"
            f"<nav><a href=\"{root}index.html\">Dashboard</a> · <a href=\"{root}archive.html\">Archive</a> · "
            f"<a href=\"{root}feed.xml\">Atom feed</a></nav>{body}</main></body></html>\n")


def _item_html(it: dict) -> str:
    e = html.escape
    srcs = " · ".join(f"<a href=\"{e(s['url'])}\">{e(s['outlet'])}</a> <span class=\"src\">{s['tier']}</span>"
                      for s in it["sources"])
    orig = f"<p class=\"meta\" lang=\"zh\">原标题：{e(it['original_title'])}</p>" if it.get("original_title") else ""
    return (f"<article><p class=\"meta\"><span class=\"i\">I {it['score']['total']:.1f}</span> · "
            f"{TOPICS[it['topic']]} · {REGIONS[it['region']]}</p><h3>{e(it['title'])}</h3>{orig}"
            f"<p>{e(it['summary'])}</p><p><b>Why it matters.</b> {e(it['why_it_matters'])}</p>"
            f"<p class=\"src\">{srcs}</p></article>")


def daily_page(cfg: Config, archive: Archive, d: date) -> str:
    iss = archive.daily(d)
    assert iss is not None
    e = html.escape
    leads = [it for it in iss["items"] if it["placement"] == "lead"]
    briefs = [it for it in iss["items"] if it["placement"] == "brief"]
    body = [f"<h1>AI Daily · {d:%a %d %b %Y}</h1>",
            "<h2>TL;DR</h2><ul>" + "".join(f"<li>{e(t)}</li>" for t in iss["tldr"]) + "</ul>",
            "<h2>Lead stories</h2>", *(_item_html(it) for it in leads)]
    if briefs:
        body.append("<h2>Briefs</h2><table>" + "".join(
            f"<tr><td class=\"i\">{it['score']['total']:.1f}</td><td><a href=\"{e(it['sources'][0]['url'])}\">"
            f"{e(it['title'])}</a></td><td class=\"meta\">{TOPICS[it['topic']]}</td></tr>" for it in briefs) + "</table>")
    cal = calendar_as_of(cfg, archive, d)
    if cal:
        body.append("<h2>Upcoming</h2><table>" + "".join(
            f"<tr><td class=\"meta\">{ev['date']}</td><td>{e(ev['title'])}</td><td class=\"meta\">{ev['confidence']}</td></tr>"
            for ev in cal) + "</table>")
    return _page(f"AI Daily · {d.isoformat()}", "".join(body), root="../")


def weekly_page(cfg: Config, archive: Archive, wid: str) -> str:
    v = weekly_view(cfg, archive, wid)
    e = html.escape
    body = [f"<h1>AI Weekly · {wid}</h1><p class=\"meta\">{v['start']} → {v['end']} · {v['n']} stories</p>"]
    if v["headline"]:
        body.append(f"<p><b>{e(v['headline'])}</b></p>")
    for th in v["themes"]:
        body.append(f"<h2>{e(th['title'])}</h2><p>{e(th['summary'])}</p><ul>" +
                    "".join(f"<li><a href=\"{e(r['url'])}\">{e(r['title'])}</a> <span class=\"i\">I {r['score']:.1f}</span></li>"
                            for r in th["items"]) + "</ul>")
    body.append("<h2>Top stories</h2><table>" + "".join(
        f"<tr><td class=\"i\">{r['score']:.1f}</td><td><a href=\"{e(r['url'])}\">{e(r['title'])}</a></td>"
        f"<td class=\"meta\">{r['date'] or ''}</td></tr>" for r in v["top"]) + "</table>")
    return _page(f"AI Weekly · {wid}", "".join(body), root="../")


def atom(cfg: Config, archive: Archive) -> str:
    base = cfg.site_url.rstrip("/") + "/" if cfg.site_url else ""
    entries: list[tuple[datetime, str]] = []
    for d in archive.daily_dates():
        iss = archive.daily(d)
        if not iss or "items" not in iss:
            continue
        updated = parse_ts(iss["window"]["end"])
        summary = " ".join(f"• {t}" for t in iss["tldr"])
        url = f"{base}daily/{d.isoformat()}.html"
        entries.append((updated, (
            f"<entry><id>tag:ai-daily,{d.isoformat()}:daily</id><title>{xesc(f'AI Daily · {d:%a %d %b %Y}')}</title>"
            f"<updated>{updated:%Y-%m-%dT%H:%M:%SZ}</updated><link href=\"{xesc(url)}\"/>"
            f"<summary>{xesc(summary)}</summary></entry>")))
    for wid in archive.weekly_ids():
        wk = archive.weekly(wid) or {}
        sun = week_bounds(wid)[1]
        updated = datetime(sun.year, sun.month, sun.day, 23, 0, tzinfo=timezone.utc)
        url = f"{base}weekly/{wid}.html"
        entries.append((updated, (
            f"<entry><id>tag:ai-daily,{wid}:weekly</id><title>{xesc(f'AI Weekly · {wid}')}</title>"
            f"<updated>{updated:%Y-%m-%dT%H:%M:%SZ}</updated><link href=\"{xesc(url)}\"/>"
            f"<summary>{xesc(wk.get('headline', ''))}</summary></entry>")))
    entries.sort(key=lambda p: p[0], reverse=True)
    newest = entries[0][0] if entries else datetime(2026, 1, 1, tzinfo=timezone.utc)
    return ("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<feed xmlns=\"http://www.w3.org/2005/Atom\">"
            f"<id>tag:ai-daily,feed</id><title>{xesc(cfg.site_title)}</title>"
            f"<updated>{newest:%Y-%m-%dT%H:%M:%SZ}</updated><link rel=\"self\" href=\"{xesc(base + 'feed.xml')}\"/>"
            + "".join(e for _, e in entries[:60]) + "</feed>\n")


def build_site(cfg: Config, archive: Archive, out: Path) -> list[Path]:
    written: list[Path] = []
    latest = archive.latest_daily()
    if latest is None:
        return written

    def put(rel: str, text: str) -> None:
        p = out / rel
        write_text(p, text)
        written.append(p)

    put("index.html", dashboard.render(cfg, archive, latest, standalone=True))
    rows = []
    for d in reversed(archive.daily_dates()):
        put(f"daily/{d.isoformat()}.html", daily_page(cfg, archive, d))
        rows.append(f"<li><a href=\"daily/{d.isoformat()}.html\">{d:%a %d %b %Y}</a></li>")
    for wid in reversed(archive.weekly_ids()):
        put(f"weekly/{wid}.html", weekly_page(cfg, archive, wid))
        rows.append(f"<li><a href=\"weekly/{wid}.html\">Weekly recap {wid}</a></li>")
    put("archive.html", _page("AI Daily · Archive", "<h1>Archive</h1><ul>" + "".join(rows) + "</ul>", root=""))
    put("feed.xml", atom(cfg, archive))
    return written
