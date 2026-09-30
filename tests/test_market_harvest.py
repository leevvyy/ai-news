"""Market model, price store, harvester parsing/clustering and the China quota (no network)."""

import json
import math
import random
import unittest
from datetime import date, datetime, timedelta, timezone

from ainews import harvest, market
from ainews.archive import Archive
from ainews.scoring import score_and_place

from helpers import TempRepo, make_item

MCFG = {"estimation_days": 60, "estimation_gap": 10, "car_days": 3,
        "benchmarks": {"": "^GSPC", ".KS": "^KS11", ".HK": "^HSI"}}


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synthetic(beta: float, alpha: float, shock_on: date | None, shock: float, n: int = 120, seed: int = 7):
    """Stock = alpha + beta·market + small noise, plus an abnormal jump on `shock_on`."""
    rng = random.Random(seed)
    days = weekdays(date(2026, 4, 1), n)
    m, s = {days[0]: 100.0}, {days[0]: 50.0}
    for a, b in zip(days, days[1:]):
        rm = rng.gauss(0.0005, 0.01)
        rs = alpha + beta * rm + rng.gauss(0, 0.002) + (shock if b == shock_on else 0.0)
        m[b], s[b] = m[a] * (1 + rm), s[a] * (1 + rs)
    return days, s, m


class MarketModel(unittest.TestCase):
    def test_ols_recovers_beta(self):
        rng = random.Random(1)
        x = [rng.gauss(0, 0.01) for _ in range(500)]
        y = [0.001 + 1.7 * a + rng.gauss(0, 0.001) for a in x]
        alpha, beta, sigma = market.ols(x, y)
        self.assertAlmostEqual(beta, 1.7, delta=0.02)
        self.assertAlmostEqual(alpha, 0.001, delta=0.0002)
        self.assertAlmostEqual(sigma, 0.001, delta=0.0002)

    def test_event_study_finds_the_shock(self):
        days, s, m = synthetic(1.5, 0.0, None, 0.0)
        shock_day = days[100]
        days, s, m = synthetic(1.5, 0.0, shock_day, 0.05)
        prices = {"NVDA": s, "^GSPC": m}
        # published the evening before, after the US close → day 0 is the shock session
        published = datetime.combine(days[99], datetime.min.time(), timezone.utc) + timedelta(hours=22)
        ev = market.event_study(MCFG, prices, "NVDA", published)
        self.assertEqual(ev.status, "ok")
        self.assertEqual(ev.day0, shock_day.isoformat())
        self.assertAlmostEqual(ev.beta, 1.5, delta=0.1)
        self.assertAlmostEqual(ev.ar[0], 0.05, delta=0.01)
        self.assertGreater(ev.t, 5)

    def test_day0_respects_exchange_close(self):
        days, s, m = synthetic(1.0, 0.0, None, 0.0)
        prices = {"005930.KS": s, "^KS11": m}
        d = days[100]
        before = datetime(d.year, d.month, d.day, 5, 0, tzinfo=timezone.utc)   # 14:00 KST, before 15:30 close
        after = datetime(d.year, d.month, d.day, 7, 0, tzinfo=timezone.utc)    # 16:00 KST, after close
        self.assertEqual(market.event_study(MCFG, prices, "005930.KS", before).day0, d.isoformat())
        self.assertEqual(market.event_study(MCFG, prices, "005930.KS", after).day0, days[101].isoformat())

    def test_statuses(self):
        days, s, m = synthetic(1.0, 0.0, None, 0.0, n=40)
        prices = {"AMD": s, "^GSPC": m}
        late = datetime.combine(days[-1], datetime.min.time(), timezone.utc) + timedelta(days=2)
        self.assertEqual(market.event_study(MCFG, prices, "AMD", late).status, "pending")
        early = datetime.combine(days[20], datetime.min.time(), timezone.utc)
        self.assertEqual(market.event_study(MCFG, prices, "AMD", early).status, "short-history")
        self.assertEqual(market.event_study(MCFG, prices, "XYZ", early).status, "no-data")
        full = synthetic(1.0, 0.0, None, 0.0, n=120)
        ev = market.event_study(MCFG, {"AMD": full[1], "^GSPC": full[2]},
                                "AMD", datetime.combine(full[0][-2], datetime.min.time(), timezone.utc))
        self.assertEqual((ev.status, ev.n), ("partial", 2))

    def test_merge_rescales_restated_history(self):
        stored = {date(2026, 9, d): 100.0 + d for d in range(1, 21)}
        fetched = {d: v / 2 for d, v in stored.items()}          # a 2:1 split restates history
        fetched[date(2026, 9, 21)] = 61.0
        merged = market.merge_series(stored, fetched)
        self.assertAlmostEqual(merged[date(2026, 9, 1)], 50.5)
        rs_before, rs_after = market.returns(stored), market.returns(merged)
        self.assertAlmostEqual(rs_before[date(2026, 9, 10)], rs_after[date(2026, 9, 10)])

    def test_mean_ci(self):
        ci = market.mean_ci([0.01, 0.02, 0.03])
        self.assertAlmostEqual(ci["mean"], 0.02)
        self.assertAlmostEqual(ci["hi"] - ci["mean"], 4.303 * 0.01 / math.sqrt(3), places=5)
        self.assertIsNone(market.mean_ci([]))


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>OpenAI ships a new model</title><link>https://example.com/a?utm_source=x</link>
<pubDate>Tue, 29 Sep 2026 17:00:00 GMT</pubDate><description>&lt;p&gt;Big &amp;amp; new.&lt;/p&gt;</description></item>
<item><title>Gardening tips for autumn</title><link>https://example.com/garden</link>
<pubDate>Tue, 29 Sep 2026 16:00:00 GMT</pubDate><description>Leaves.</description></item>
<item><title>Old news about GPUs</title><link>https://example.com/old</link>
<pubDate>Mon, 21 Sep 2026 16:00:00 GMT</pubDate></item>
<item><title>OpenAI ships new model to developers - Reuters</title><link>https://news.example.com/r</link>
<pubDate>Tue, 29 Sep 2026 18:00:00 GMT</pubDate><source url="https://www.reuters.com">Reuters</source></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>DeepSeek \xe5\x8f\x91\xe5\xb8\x83\xe6\x96\xb0\xe6\xa8\xa1\xe5\x9e\x8b</title>
<link rel="alternate" href="https://example.cn/ds"/><updated>2026-09-29T09:42:00Z</updated>
<summary>\xe5\xa4\xa7\xe6\xa8\xa1\xe5\x9e\x8b</summary></entry></feed>"""


class Harvest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        (self.repo.dir / "feeds.toml").write_text(
            '[[feed]]\nname = "Test RSS"\nurl = "https://feed.example.com/rss"\ntier = "press"\nlang = "en"\nfilter = true\n\n'
            '[[feed]]\nname = "Test Atom"\nurl = "https://feed.example.cn/atom"\ntier = "trade"\nlang = "zh"\n\n'
            '[[feed]]\nname = "Broken"\nurl = "https://down.example.com/rss"\ntier = "press"\nlang = "en"\n', "utf-8")

    def tearDown(self):
        self.repo.cleanup()

    def fetcher(self, url: str) -> bytes:
        if "down" in url:
            raise OSError("connection refused")
        return RSS if "rss" in url else ATOM

    def test_parse_filter_window_and_clusters(self):
        now = datetime(2026, 9, 29, 21, 7, tzinfo=timezone.utc)
        raw = harvest.harvest_news(self.repo.cfg, now, fetcher=self.fetcher)
        titles = [it["title"] for it in raw["items"]]
        self.assertIn("OpenAI ships a new model", titles)
        self.assertNotIn("Gardening tips for autumn", titles)       # keyword filter
        self.assertNotIn("Old news about GPUs", titles)             # older than since_hours
        self.assertIn("OpenAI ships new model to developers - Reuters", titles)  # suffix stripped for gnews only
        self.assertTrue(any(it["lang"] == "zh" for it in raw["items"]))
        first = next(it for it in raw["items"] if it["title"] == "OpenAI ships a new model")
        self.assertEqual(first["summary"], "Big & new.")
        broken = next(f for f in raw["feeds"] if f["name"] == "Broken")
        self.assertFalse(broken["ok"])
        self.assertIn("connection refused", broken["error"])
        top = raw["clusters"][0]
        self.assertEqual(top["n_outlets"], 2)  # the <source> tag makes Reuters a second outlet
        self.assertEqual(top["outlets"], ["Reuters", "Test RSS"])
        self.assertEqual(len(top["item_ids"]), 2)
        text = harvest.digest(raw)
        self.assertIn("failed feeds: Broken", text)

    def test_digest_flags_stale_harvest(self):
        now = datetime(2026, 9, 29, 21, 7, tzinfo=timezone.utc)
        raw = harvest.harvest_news(self.repo.cfg, now, fetcher=self.fetcher)
        self.assertNotIn("STALE", harvest.digest(raw, now=now + timedelta(hours=1)))
        self.assertIn("STALE: this harvest is 11 h old", harvest.digest(raw, now=now + timedelta(hours=11)))

    def test_cluster_tokens_cjk_bigrams(self):
        toks = harvest.cluster_tokens("DeepSeek发布新模型")
        self.assertIn("deepseek", toks)
        self.assertIn("发布", toks)
        self.assertIn("模型", toks)

    def test_hn_and_hf_parsers(self):
        hn = harvest.parse_hn(json.dumps({"hits": [{"objectID": "1", "title": "Show HN: LLM", "url": None,
                                                     "points": 321, "num_comments": 45, "created_at_i": 1790700000}]}).encode())
        self.assertEqual(hn[0]["signals"]["hn_points"], 321)
        self.assertTrue(hn[0]["url"].startswith("https://news.ycombinator.com/item?id=1"))
        hf = harvest.parse_hf(json.dumps([{"paper": {"id": "2609.00001", "title": "A paper", "upvotes": 42,
                                                      "summary": "s"}, "publishedAt": "2026-09-29T00:00:00Z"}]).encode())
        self.assertEqual((hf[0]["url"], hf[0]["signals"]["hf_upvotes"]), ("https://arxiv.org/abs/2609.00001", 42))


class ChinaQuota(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()

    def tearDown(self):
        self.repo.cleanup()

    def test_swaps_best_china_items_into_briefs(self):
        items = [make_item(f"us{i}", entity=f"US{i}", impact=0.9 - 0.02 * i) for i in range(16)]
        items += [make_item(f"cn{i}", entity=f"CN{i}", impact=0.2 - 0.05 * i, region="cn") for i in range(3)]
        ranked = score_and_place(self.repo.cfg, Archive(self.repo.cfg), items, date(2026, 9, 29))
        shown = [it for it in ranked if it["placement"] in ("lead", "brief")]
        self.assertEqual(sum(it["region"] == "cn" for it in shown), self.repo.cfg.china_quota)
        self.assertEqual({it["id"] for it in shown if it.get("quota")}, {"cn0", "cn1"})
        self.assertEqual(len(shown), self.repo.cfg.leads + self.repo.cfg.briefs)
        noted_us = [it["id"] for it in ranked if it["placement"] == "noted" and it["region"] == "us"]
        self.assertEqual(noted_us, ["us13", "us14", "us15"])  # us13, us14 were the weakest briefs; us15 was noted

    def test_no_swap_when_quota_met(self):
        items = [make_item(f"cn{i}", entity=f"CN{i}", impact=0.9, region="cn") for i in range(3)]
        items += [make_item(f"us{i}", entity=f"US{i}", impact=0.5) for i in range(5)]
        ranked = score_and_place(self.repo.cfg, Archive(self.repo.cfg), items, date(2026, 9, 29))
        self.assertFalse(any(it.get("quota") for it in ranked))


if __name__ == "__main__":
    unittest.main()


class MarketPipeline(unittest.TestCase):
    """Synthetic prices on disk → build renders the market section and dashboard chips."""

    def test_build_renders_market(self):
        import contextlib
        import io
        from ainews.cli import main
        from helpers import make_issue
        repo = TempRepo()
        try:
            days, s, m = synthetic(1.2, 0.0, None, 0.0, n=150)
            (repo.dir / "data" / "market").mkdir(parents=True)
            (repo.dir / "data" / "market" / "prices.csv").write_text(
                market.dump_prices({"NVDA": s, "^GSPC": m}), "utf-8")
            words = "alpha bravo charlie delta echo foxtrot golf hotel".split()
            items = [make_item(f"s-{w}", title=f"{w.title()} story about {w} things", entity=f"L{i}",
                               impact=0.9 - 0.05 * i, tickers=["NVDA"] if i == 0 else [])
                     for i, w in enumerate(words)]
            repo.put_daily(make_issue("2026-09-29", items))
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--root", str(repo.dir), "build", "2026-09-29"]), 0)
                self.assertEqual(main(["--root", str(repo.dir), "check"]), 0)
            md = (repo.dir / "issues" / "daily" / "2026-09-29.md").read_text("utf-8")
            self.assertIn("## Market read-through", md)
            self.assertIn("`NVDA`", md)
            html = (repo.dir / "artifacts" / "latest.html").read_text("utf-8")
            self.assertIn('"bench":"^GSPC"', html)
        finally:
            repo.cleanup()


class FeedRobustness(unittest.TestCase):
    def test_sanitize_repairs_common_breakage(self):
        broken = ("<?xml version='1.0'?><rss><channel><item><title>A&B \x0bpartners&nbsp;now &amp; later "
                  "&#169; &unknownthing;</title><link>https://example.com/x?a=1&b=2</link>"
                  "<pubDate>Tue, 29 Sep 2026 17:00:00 GMT</pubDate></item></channel></rss>").encode()
        items = harvest.parse_feed(broken)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "A&B partners\xa0now & later © &unknownthing;")
        self.assertEqual(items[0]["url"], "https://example.com/x?a=1&b=2")

    def test_disabled_feeds_are_skipped(self):
        repo = TempRepo()
        try:
            (repo.dir / "feeds.toml").write_text(
                '[[feed]]\nname = "On"\nurl = "https://a.example.com/rss"\n\n'
                '[[feed]]\nname = "Off"\nurl = "https://b.example.com/rss"\nenabled = false\n', "utf-8")
            self.assertEqual([f["name"] for f in harvest.load_feeds(repo.cfg)], ["On"])
        finally:
            repo.cleanup()

    def test_cluster_prefers_outlet_url_over_google_news(self):
        base = {"lang": "en", "tier": "press", "summary": "", "outlet": "X"}
        items = [{**base, "id": "g", "title": "Chipmaker unveils new accelerator today", "via": "gnews",
                  "url": "https://news.google.com/rss/articles/abc", "outlet": "Reuters",
                  "published_at": "2026-09-29T10:00:00Z"},
                 {**base, "id": "d", "title": "Chipmaker unveils new accelerator", "via": "rss",
                  "url": "https://outlet.example.com/story", "published_at": "2026-09-29T11:00:00Z"}]
        [c] = harvest.cluster(items, 0.4)
        self.assertEqual(c["url"], "https://outlet.example.com/story")
        self.assertEqual(c["n_outlets"], 2)
