"""Learning track: validation of `learn` entries, gap ranking, tracker, and rendering."""

import contextlib
import io
import json
import unittest
from datetime import date

from ainews.archive import Archive
from ainews.cli import main
from ainews.learn import matches, suggest, tracker
from ainews.validate import validate_daily

from helpers import TempRepo, make_issue, make_item


def learn_entry(lid="kv-cache-sizing", gap="llm-internals", item_ids=("kv-bug",), **extra):
    return {"id": lid, "gap": gap, "title": "Size a KV cache", "item_ids": list(item_ids),
            "why_today": "A serving bug came from an undersized KV pool.",
            "explanation": "Keys and values are cached per layer and token.",
            "math": "M = 2 L n_{kv} d T b", "python": "print(2 * 60 * 8 * 128 * 2)\n", "minutes": 15, **extra}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Learn(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.cfg = self.repo.cfg
        self.items = [make_item("kv-bug", title="Cloud host leaks data through a KV-cache bug", impact=0.7),
                      make_item("ipo", title="Lab files S-1 for an IPO", entity="LabB", topic="business"),
                      *[make_item(f"filler-{i}", title=f"Filler {w} story", entity=f"Lab{i}")
                        for i, w in enumerate("one two three four five six".split())]]

    def tearDown(self):
        self.repo.cleanup()

    def test_valid_entry_and_errors(self):
        iss = make_issue("2026-09-29", self.items)
        iss["learn"] = [learn_entry()]
        self.assertTrue(validate_daily(self.cfg, iss).ok, validate_daily(self.cfg, iss).errors)

        iss["learn"] = [learn_entry(gap="astrology", item_ids=("nope",), minutes=0, python="def f(:\n"),
                        learn_entry(lid="b"), learn_entry(lid="c"), learn_entry(lid="d")]
        errs = " ".join(validate_daily(self.cfg, iss).errors)
        for needle in ("at most 3", "'gap' must be one of", "'nope' is not an item", "'minutes'", "does not parse"):
            self.assertIn(needle, errs)

    def test_old_issues_without_learn_still_valid(self):
        self.assertTrue(validate_daily(self.cfg, make_issue("2026-09-29", self.items)).ok)

    def test_keyword_match_is_word_start(self):
        g = self.cfg.gaps["llm-eval"]
        self.assertIn("eval", matches(g, {"title": "New evaluation suite", "summary": "", "why_it_matters": ""}))
        self.assertEqual(matches(g, {"title": "A medieval castle", "summary": "", "why_it_matters": ""}), [])

    def test_suggest_ranks_by_priority_hook_and_recency(self):
        archive = Archive(self.cfg)
        rows = suggest(self.cfg, archive, date(2026, 9, 29), self.items)
        by = {r["gap"]: r for r in rows}
        internals = by["llm-internals"]
        self.assertGreater(internals["hook"], 0)
        self.assertEqual(internals["hooks"][0]["id"], "kv-bug")
        g = self.cfg.gaps["llm-internals"]
        self.assertAlmostEqual(internals["score"], g.priority * (1 + internals["hook"]), places=2)
        self.assertEqual([r["score"] for r in rows], sorted((r["score"] for r in rows), reverse=True))

        prev = make_issue("2026-09-28", [make_item("old", published_at="2026-09-28T10:00:00Z")])
        prev["learn"] = [learn_entry(item_ids=("old",))]
        self.repo.put_daily(prev)
        damped = {r["gap"]: r for r in suggest(self.cfg, Archive(self.cfg), date(2026, 9, 29), self.items)}
        self.assertEqual(damped["llm-internals"]["days_since"], 1)
        self.assertLess(damped["llm-internals"]["score"], internals["score"])

    def test_build_renders_section_tracker_and_check_passes(self):
        prev = make_issue("2026-09-28", [make_item("old", published_at="2026-09-28T10:00:00Z")])
        prev["learn"] = [learn_entry(lid="earlier", item_ids=("old",), minutes=10)]
        self.repo.put_daily(prev)
        iss = make_issue("2026-09-29", self.items)
        iss["learn"] = [learn_entry(), learn_entry(lid="ipo-maths", gap="domain-ai", item_ids=("ipo",))]
        self.repo.put_daily(iss)
        root = str(self.repo.dir)
        self.assertEqual(run("--root", root, "build", "2026-09-28")[0], 0)
        self.assertEqual(run("--root", root, "build", "2026-09-29")[0], 0)
        md = (self.repo.dir / "issues" / "daily" / "2026-09-29.md").read_text("utf-8")
        self.assertIn("## Learn this", md)
        self.assertIn("### L2 · Size a KV cache", md)
        self.assertIn("```python\nprint(2 * 60 * 8 * 128 * 2)\n```", md)
        self.assertIn("streak 2 issue(s) in a row", md)
        tr = tracker(self.cfg, Archive(self.cfg), date(2026, 9, 29))
        self.assertEqual((tr["total"], tr["minutes"], tr["streak"]), (3, 40, 2))
        html = (self.repo.dir / "artifacts" / "latest.html").read_text("utf-8")
        self.assertIn('"learn":[{', html)
        self.assertEqual(run("--root", root, "check")[0], 0)
        code, out, _ = run("--root", root, "learn")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["already"], ["kv-cache-sizing", "ipo-maths"])

    def test_issue_without_learn_renders_no_section(self):
        self.repo.put_daily(make_issue("2026-09-29", self.items))
        self.assertEqual(run("--root", str(self.repo.dir), "build")[0], 0)
        md = (self.repo.dir / "issues" / "daily" / "2026-09-29.md").read_text("utf-8")
        self.assertNotIn("Learn this", md)


if __name__ == "__main__":
    unittest.main()
