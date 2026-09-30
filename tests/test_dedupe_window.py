import unittest
from datetime import date, datetime, timezone

from ainews.archive import Archive
from ainews.dedupe import canonical_url, find_conflicts, jaccard, title_tokens
from ainews.timewin import compute_window

from helpers import TempRepo, make_issue, make_item


class Dedupe(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()

    def tearDown(self):
        self.repo.cleanup()

    def test_canonical_url(self):
        self.assertEqual(canonical_url("http://www.Example.com/a/b/?utm_source=x&id=3#frag"),
                         "https://example.com/a/b?id=3")
        self.assertEqual(canonical_url("https://example.com/a/b"), canonical_url("https://example.com/a/b/"))

    def test_title_similarity(self):
        a = title_tokens("OpenAI launches GPT-6.1 Sol at DevDay")
        b = title_tokens("OpenAI launches GPT-6.1 Sol model at DevDay 2026")
        self.assertGreaterEqual(jaccard(a, b), 0.5)

    def test_conflicts_against_archive_and_follow_up(self):
        old = make_item("lab-release", title="LabA releases Model Nine with long context",
                        published_at="2026-09-27T10:00:00Z")
        self.repo.put_daily(make_issue("2026-09-27", [old]))
        archive = Archive(self.repo.cfg)
        again = make_item("lab-release-2", title="LabA releases Model Nine with long context window")
        clash = find_conflicts(self.repo.cfg, archive, [again], date(2026, 9, 29))
        self.assertEqual([c.reason for c in clash], ["title"])
        again["follow_up_of"] = "lab-release"
        self.assertEqual(find_conflicts(self.repo.cfg, archive, [again], date(2026, 9, 29)), [])

    def test_same_url_same_day(self):
        a, b = make_item("a"), make_item("b")
        b["sources"][0]["url"] = a["sources"][0]["url"] + "?utm_medium=feed"
        clash = find_conflicts(self.repo.cfg, Archive(self.repo.cfg), [a, b], date(2026, 9, 29))
        self.assertEqual([c.reason for c in clash], ["url"])


class Window(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()

    def tearDown(self):
        self.repo.cleanup()

    def test_first_run_is_24h_dated_yesterday(self):
        now = datetime(2026, 9, 29, 21, 48, tzinfo=timezone.utc)  # 05:48 UTC+8 on the 30th
        w = compute_window(self.repo.cfg, Archive(self.repo.cfg), now)
        self.assertEqual(w.hours, 24)
        self.assertEqual(w.issue_date, date(2026, 9, 29))

    def test_chains_from_previous_end_and_caps_gaps(self):
        self.repo.put_daily(make_issue("2026-09-29", [make_item("a")],
                                       start="2026-09-28T22:00:00Z", end="2026-09-29T22:00:00Z"))
        archive = Archive(self.repo.cfg)
        w = compute_window(self.repo.cfg, archive, datetime(2026, 9, 30, 21, 50, tzinfo=timezone.utc))
        self.assertEqual(w.start, datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(w.issue_date, date(2026, 9, 30))
        self.assertFalse(w.gap)
        late = compute_window(self.repo.cfg, archive, datetime(2026, 10, 5, 21, 50, tzinfo=timezone.utc))
        self.assertTrue(late.gap)
        self.assertEqual(late.hours, self.repo.cfg.max_window_hours)

    def test_sunday_issue_closes_week(self):
        now = datetime(2026, 10, 4, 21, 48, tzinfo=timezone.utc)  # Monday 05:48 UTC+8
        w = compute_window(self.repo.cfg, Archive(self.repo.cfg), now)
        self.assertEqual(w.issue_date, date(2026, 10, 4))
        self.assertTrue(w.closes_week)
        self.assertEqual(w.week, "2026-W40")


if __name__ == "__main__":
    unittest.main()
