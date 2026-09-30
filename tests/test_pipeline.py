"""End-to-end: build → check passes → hand edit → check fails; validation errors block builds."""

import contextlib
import io
import json
import unittest

from ainews.cli import main
from ainews.config import load_config
from ainews.validate import validate_daily

from helpers import TempRepo, make_issue, make_item


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima".split()
        items = [make_item(f"story-{i}", title=f"{w.title()} ships {w}-scale {w}ware update", entity=f"Lab{i % 4}",
                           impact=0.3 + 0.05 * i, topic=("models", "research", "compute", "policy")[i % 4])
                 for i, w in enumerate(words)]
        items[3]["sources"].append({"url": "https://zh.example.cn/3", "outlet": "中文媒体", "lang": "zh",
                                    "tier": "trade", "title": "中文标题"})
        items[3]["original_title"] = "中文标题"
        issue = make_issue("2026-09-29", items)
        issue["upcoming"] = [{"date": "2026-10-07", "title": "Example conference keynote", "kind": "conference",
                              "confidence": "confirmed", "url": "https://example.com/conf"}]
        self.path = self.repo.put_daily(issue)
        self.root = str(self.repo.dir)

    def tearDown(self):
        self.repo.cleanup()

    def test_build_then_check_roundtrip(self):
        code, out, err = run("--root", self.root, "build", "2026-09-29")
        self.assertEqual(code, 0, err)
        md = (self.repo.dir / "issues" / "daily" / "2026-09-29.md").read_text("utf-8")
        self.assertIn("## Lead stories", md)
        self.assertIn("〔中文〕", md)  # the Chinese-sourced item ranks as a brief here
        self.assertNotIn("``", md)
        self.assertIn("Example conference keynote", md)
        html = (self.repo.dir / "artifacts" / "latest.html").read_text("utf-8")
        self.assertTrue(html.startswith("<title>AI Daily</title>"))
        self.assertNotIn("<!doctype", html.lower())
        self.assertNotIn("__PAYLOAD__", html)
        stored = json.loads(self.path.read_text("utf-8"))
        self.assertEqual(sum(it["placement"] == "lead" for it in stored["items"]), self.repo.cfg.leads)

        code, out, err = run("--root", self.root, "check")
        self.assertEqual(code, 0, err)

        md_path = self.repo.dir / "issues" / "daily" / "2026-09-29.md"
        md_path.write_text(md.replace("TL;DR", "TLDR"), "utf-8")
        code, _, err = run("--root", self.root, "check")
        self.assertEqual(code, 1)
        self.assertIn("stale or hand-edited", err)

    def test_summary_and_site(self):
        self.assertEqual(run("--root", self.root, "build")[0], 0)
        code, out, _ = run("--root", self.root, "summary")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("AI Daily Tue 29 Sep:"))
        out_dir = self.repo.dir / "site"
        self.assertEqual(run("--root", self.root, "site", "--out", str(out_dir))[0], 0)
        self.assertTrue((out_dir / "index.html").read_text("utf-8").startswith("<!doctype html>"))
        self.assertIn("<feed", (out_dir / "feed.xml").read_text("utf-8"))

    def test_invalid_issue_blocks_build(self):
        issue = json.loads(self.path.read_text("utf-8"))
        issue["items"][0]["topic"] = "gossip"
        issue["items"][1]["published_at"] = "2026-09-20T00:00:00Z"
        self.path.write_text(json.dumps(issue), "utf-8")
        code, _, err = run("--root", self.root, "build")
        self.assertEqual(code, 1)
        self.assertIn("'topic' must be one of", err)
        self.assertIn("outside the window", err)

    def test_validation_warnings(self):
        cfg = load_config(self.repo.dir)
        issue = make_issue("2026-09-29", [make_item("only-social", tiers=("social",))])
        issue["items"][0]["sources"][0]["lang"] = "zh"
        rep = validate_daily(cfg, issue)
        self.assertTrue(rep.ok)
        joined = " ".join(rep.warnings)
        self.assertIn("original_title", joined)
        self.assertIn("only social sources", joined)


if __name__ == "__main__":
    unittest.main()
