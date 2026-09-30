"""Command line: `python3 -m ainews <command>`.

    window   [--now TS]                     where the next issue's window starts/ends, what to avoid repeating
    new      [--now TS] [--start TS]        create data/daily/<date>.json skeleton for the researcher
    build    [DATE]                         validate → dedupe → score → place → Markdown + dashboard (+ weekly on Sundays)
    weekly   WEEK                           score back-fill items, validate themes, render the weekly recap
    summary  [DATE]                         text for the routine's push notification
    raw      [--file PATH] [--top N]        digest of the nightly harvest (clusters, primary items, papers, HN)
    harvest  [--now TS] [--no-news|--no-prices]   network: feeds → data/raw, prices → data/market (GitHub Actions)
    render                                  re-render every generated file (after a price update)
    check                                   CI: validate everything and verify generated files are reproducible
    site     [--out DIR]                    static site + Atom feed (publish-ready)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import dashboard, render_md
from .aggregate import calendar_as_of, topic_counts
from .archive import Archive, dump_json, fmt_ts, load_json, parse_ts, week_bounds, week_id, write_text
from .config import TOPICS, Config, load_config
from .dedupe import find_conflicts
from .scoring import item_local_date, score_and_place, score_item
from .site import build_site
from .timewin import compute_window
from . import harvest as harvest_mod
from .validate import Report, validate_daily, validate_weekly


# ---- helpers ---------------------------------------------------------------
def _print_report(rep: Report, label: str) -> None:
    for w in rep.warnings:
        print(f"  warn  {label}: {w}", file=sys.stderr)
    for e in rep.errors:
        print(f"  ERROR {label}: {e}", file=sys.stderr)


def _ts(value: str | None) -> datetime | None:
    return parse_ts(value) if value else None


def _latest(archive: Archive, arg: str | None) -> date:
    if arg:
        return date.fromisoformat(arg)
    d = archive.latest_daily()
    if d is None:
        raise SystemExit("no daily issues in data/daily/")
    return d


# ---- pipeline stages -----------------------------------------------------------
def score_daily(cfg: Config, archive: Archive, d: date) -> Report:
    """Validate, dedupe and score one daily issue in memory (archive cache updated)."""
    iss = load_json(archive.daily_path(d))
    rep = validate_daily(cfg, iss, d)
    if rep.ok:
        for c in find_conflicts(cfg, archive, iss["items"], d):
            rep.err("dedupe", str(c) + " — drop the item or set follow_up_of")
    if rep.ok:
        iss["items"] = score_and_place(cfg, archive, iss["items"], d)
        archive.put_daily(d, iss)
    return rep


def score_weekly(cfg: Config, archive: Archive, wid: str) -> Report:
    path = archive.weekly_path(wid)
    wk = load_json(path) if path.exists() else {"week": wid, "headline": "", "themes": [], "items": [], "upcoming": []}
    rep = validate_weekly(cfg, wk, wid)
    if not rep.ok:
        return rep
    items = wk.get("items", [])
    for it in items:
        day = item_local_date(cfg, it)
        cohort = [x for x in items if item_local_date(cfg, x) == day]
        it["score"] = score_item(cfg, archive, it, day, cohort)
    archive.put_weekly(wid, wk)
    mon, sun = week_bounds(wid)
    known = {it["id"] for _, it in archive.items_between(mon, sun)}
    for i, th in enumerate(wk.get("themes", [])):
        for ref in th.get("item_ids", []):
            if ref not in known:
                rep.err(f"weekly.themes[{i}]", f"item_id {ref!r} is not an item of {wid}")
    return rep


def render_all(cfg: Config, archive: Archive) -> dict[Path, str]:
    """Every generated file, from the (scored) archive. Cheap and deterministic, so builds
    re-render everything and `check` compares against exactly the same function."""
    out: dict[Path, str] = {}
    for d in archive.daily_dates():
        iss = archive.daily(d)
        if iss and all("score" in it for it in iss.get("items", [])):
            out[cfg.daily_md / f"{d.isoformat()}.md"] = render_md.render_daily(cfg, archive, d)
    for wid in archive.weekly_ids():
        out[cfg.weekly_md / f"{wid}.md"] = render_md.render_weekly(cfg, archive, wid)
    latest = archive.latest_daily()
    if latest and all("score" in it for it in (archive.daily(latest) or {}).get("items", [])):
        out[cfg.dashboard_path] = dashboard.render(cfg, archive, latest)
    return out


def write_outputs(cfg: Config, outputs: dict[Path, str]) -> None:
    for p, text in outputs.items():
        if write_text(p, text):
            print("wrote " + str(p.relative_to(cfg.root)))


# ---- commands ----------------------------------------------------------------
def cmd_window(cfg: Config, args: argparse.Namespace) -> int:
    archive = Archive(cfg)
    win = compute_window(cfg, archive, _ts(args.now))
    lo = win.issue_date - timedelta(days=cfg.dedupe_lookback_days)
    recent = [{"date": d.isoformat(), "id": it["id"], "title": it["title"]}
              for d, it in archive.items_between(lo, win.issue_date - timedelta(days=1))]
    out = {
        "issue_date": win.issue_date.isoformat(),
        "window": win.as_json(),
        "window_local": f"{win.start.astimezone(cfg.tz):%a %d %b %H:%M} → {win.end.astimezone(cfg.tz):%a %d %b %H:%M} {cfg.tz_label}",
        "hours": round(win.hours, 2),
        "gap": win.gap,
        "exists": archive.daily_path(win.issue_date).exists(),
        "weekly_due": win.week if win.closes_week else None,
        "recent_items": recent,
        "open_calendar": calendar_as_of(cfg, archive, win.issue_date),
        "raw_file": str(p.relative_to(cfg.root)) if (p := harvest_mod.latest_raw(cfg)) else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_new(cfg: Config, args: argparse.Namespace) -> int:
    archive = Archive(cfg)
    win = compute_window(cfg, archive, _ts(args.now))
    start = _ts(args.start) or win.start
    if start >= win.end:
        raise SystemExit("--start must be before the window end")
    midpoint = start + (win.end - start) / 2
    d = midpoint.astimezone(cfg.tz).date()
    path = archive.daily_path(d)
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    dump_json({"date": d.isoformat(), "window": {"start": fmt_ts(start), "end": fmt_ts(win.end)},
               "tldr": [], "items": [], "upcoming": [], "notes": ""}, path)
    print(path)
    return 0


def cmd_build(cfg: Config, args: argparse.Namespace) -> int:
    archive = Archive(cfg)
    d = _latest(archive, args.date)
    rep = score_daily(cfg, archive, d)
    _print_report(rep, d.isoformat())
    if not rep.ok:
        print(f"build {d}: FAILED with {len(rep.errors)} error(s)", file=sys.stderr)
        return 1
    dump_json(archive.daily(d), archive.daily_path(d))
    wid = week_id(d)
    if archive.weekly_path(wid).exists():
        wrep = score_weekly(cfg, archive, wid)
        _print_report(wrep, wid)
        if not wrep.ok:
            return 1
        dump_json(archive.weekly(wid), archive.weekly_path(wid))
    write_outputs(cfg, render_all(cfg, archive))
    iss = archive.daily(d)
    leads = [it for it in iss["items"] if it["placement"] == "lead"]
    print(f"build {d}: {len(iss['items'])} items, {len(leads)} leads, top I = {iss['items'][0]['score']['total']}")
    if d.isoweekday() == 7 and not (archive.weekly(week_id(d)) or {}).get("themes"):
        print(f"NOTE: {week_id(d)} closes today; write data/weekly/{week_id(d)}.json themes, "
              f"then run `python3 -m ainews weekly {week_id(d)}`", file=sys.stderr)
    return 0


def cmd_weekly(cfg: Config, args: argparse.Namespace) -> int:
    archive = Archive(cfg)
    rep = score_weekly(cfg, archive, args.week)
    _print_report(rep, args.week)
    if not rep.ok:
        return 1
    dump_json(archive.weekly(args.week), archive.weekly_path(args.week))
    write_outputs(cfg, render_all(cfg, archive))
    return 0


def cmd_summary(cfg: Config, args: argparse.Namespace) -> int:
    archive = Archive(cfg)
    d = _latest(archive, args.date)
    iss = archive.daily(d)
    assert iss is not None
    leads = [it for it in iss["items"] if it.get("placement") == "lead"]
    head = leads[0] if leads else iss["items"][0]
    lines = [f"AI Daily {d:%a %d %b}: {head['title']} (I {head['score']['total']:.0f}).",
             *[f"• {t}" for t in iss["tldr"]],
             "Leads: " + " | ".join(f"{it['title']} [{it['score']['total']:.0f}]" for it in leads[1:]),
             f"{len(iss['items'])} stories · topics: " + ", ".join(
                 f"{TOPICS[k].split(' ')[0]} {n}" for k, n in topic_counts(iss["items"]).items() if n)]
    if cfg.publish_enabled and cfg.site_url:
        lines.append(f"Web: {cfg.site_url.rstrip('/')}/daily/{d.isoformat()}.html")
    if cfg.artifact_url:
        lines.append(f"Dashboard: {cfg.artifact_url}")
    if cfg.repo_url:
        lines.append(f"Archive: {cfg.repo_url}/blob/main/issues/daily/{d.isoformat()}.md")
    print("\n".join(lines))
    return 0


def cmd_check(cfg: Config, args: argparse.Namespace) -> int:
    """Rebuild everything in memory from the research JSON and diff against the repo."""
    archive = Archive(cfg)
    failed = 0
    stored = {d: load_json(archive.daily_path(d)) for d in archive.daily_dates()}
    for d in archive.daily_dates():
        rep = score_daily(cfg, archive, d)
        _print_report(rep, d.isoformat())
        if not rep.ok:
            failed += 1
            continue
        if archive.daily(d) != stored[d]:
            print(f"  ERROR {d}: stored scores/placement differ from a fresh build; run `python3 -m ainews build {d}`",
                  file=sys.stderr)
            failed += 1
    for wid in archive.weekly_ids():
        before = load_json(archive.weekly_path(wid))
        rep = score_weekly(cfg, archive, wid)
        _print_report(rep, wid)
        if not rep.ok:
            failed += 1
        elif archive.weekly(wid) != before:
            print(f"  ERROR {wid}: stored weekly scores differ; run `python3 -m ainews weekly {wid}`", file=sys.stderr)
            failed += 1
    if failed:
        print(f"check: {failed} problem(s) in research data", file=sys.stderr)
        return 1
    expected = render_all(cfg, archive)
    for p, text in expected.items():
        if not p.exists() or p.read_text("utf-8") != text:
            print(f"  ERROR {p.relative_to(cfg.root)} is stale or hand-edited; rebuild it", file=sys.stderr)
            failed += 1
    print(f"check: {len(archive.daily_dates())} daily, {len(archive.weekly_ids())} weekly, "
          f"{len(expected)} generated files — {'OK' if not failed else 'FAILED'}")
    return 1 if failed else 0


def cmd_raw(cfg: Config, args: argparse.Namespace) -> int:
    path = Path(args.file) if args.file else harvest_mod.latest_raw(cfg)
    if path is None or not path.exists():
        print("no harvest file in data/raw/ (the nightly harvester has not run yet); research with WebSearch only")
        return 1
    print(f"# {path.relative_to(cfg.root) if path.is_absolute() and cfg.root in path.parents else path}")
    from datetime import timezone
    now = _ts(args.now) or datetime.now(timezone.utc)
    print(harvest_mod.digest(harvest_mod.load_raw(path), args.top, now))
    return 0


def cmd_harvest(cfg: Config, args: argparse.Namespace) -> int:
    from datetime import timezone
    now = _ts(args.now) or datetime.now(timezone.utc).replace(microsecond=0)
    archive = Archive(cfg)
    ok = True
    if not args.no_news:
        raw = harvest_mod.harvest_news(cfg, now)
        path = harvest_mod.write_raw(cfg, raw, now)
        good = sum(f["ok"] for f in raw["feeds"])
        print(f"news: {raw['n_items']} items, {len(raw['clusters'])} clusters, feeds ok {good}/{len(raw['feeds'])} → "
              f"{path.relative_to(cfg.root)}")
        for f in raw["feeds"]:
            if not f["ok"]:
                print(f"  feed failed: {f['name']}: {f.get('error', '')}")
        for gone in harvest_mod.prune_raw(cfg, now.astimezone(cfg.tz).date()):
            print(f"  pruned {gone.relative_to(cfg.root)}")
        ok &= good > 0
    if not args.no_prices:
        status = harvest_mod.harvest_prices(cfg, archive, now.date())
        good = sum(st["ok"] for st in status)
        print(f"prices: {good}/{len(status)} symbols updated → {cfg.prices_path.relative_to(cfg.root)}")
        for st in status:
            if not st["ok"]:
                print(f"  price failed: {st['symbol']}: {st['error']}")
        ok &= good > 0
    return 0 if ok else 1


def cmd_render(cfg: Config, args: argparse.Namespace) -> int:
    write_outputs(cfg, render_all(cfg, Archive(cfg)))
    return 0


def cmd_site(cfg: Config, args: argparse.Namespace) -> int:
    written = build_site(cfg, Archive(cfg), Path(args.out))
    print(f"site: {len(written)} files in {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ainews", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="repository root (default: auto)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("window"); p.add_argument("--now")
    p = sub.add_parser("new"); p.add_argument("--now"); p.add_argument("--start"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("build"); p.add_argument("date", nargs="?")
    p = sub.add_parser("weekly"); p.add_argument("week")
    p = sub.add_parser("summary"); p.add_argument("date", nargs="?")
    sub.add_parser("check")
    p = sub.add_parser("raw"); p.add_argument("--file"); p.add_argument("--top", type=int, default=40)
    p.add_argument("--now")
    p = sub.add_parser("harvest"); p.add_argument("--now"); p.add_argument("--no-news", action="store_true")
    p.add_argument("--no-prices", action="store_true")
    sub.add_parser("render")
    p = sub.add_parser("site"); p.add_argument("--out", default="site")
    args = ap.parse_args(argv)
    cfg = load_config(args.root) if args.root else load_config()
    return {"window": cmd_window, "new": cmd_new, "build": cmd_build, "weekly": cmd_weekly,
            "summary": cmd_summary, "check": cmd_check, "site": cmd_site, "raw": cmd_raw,
            "harvest": cmd_harvest, "render": cmd_render}[args.cmd](cfg, args)
