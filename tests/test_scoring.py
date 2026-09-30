import math
import unittest
from datetime import date

from ainews.archive import Archive
from ainews.scoring import breadth, credibility, is_verified, momentum, score_and_place, score_item

from helpers import TempRepo, make_issue, make_item

R = {"primary": 0.6, "press": 0.45, "trade": 0.25, "social": 0.1}


class Formulas(unittest.TestCase):
    def test_credibility_is_noisy_or_over_distinct_domains(self):
        src = [{"url": "https://a.example.com/1", "tier": "primary"},
               {"url": "https://b.example.com/2", "tier": "press"}]
        self.assertAlmostEqual(credibility(src, R), 1 - 0.4 * 0.55)
        # three links on one domain count once, at that domain's best tier
        same = [{"url": f"https://www.a.example.com/{i}", "tier": t} for i, t in enumerate(("social", "press", "trade"))]
        self.assertAlmostEqual(credibility(same, R), 0.45)
        # only the top-n outlets count, so piling on links saturates late
        many = [{"url": f"https://p{i}.example.com/", "tier": "press"} for i in range(8)]
        self.assertAlmostEqual(credibility(many, R, top_n=3), 1 - 0.55 ** 3)

    def test_breadth_and_momentum_saturate(self):
        self.assertAlmostEqual(breadth(3, 3.0), 1 - math.exp(-1))
        self.assertLess(breadth(1, 3.0), breadth(10, 3.0))
        self.assertAlmostEqual(momentum(2, 0.0), 1 - math.exp(-2))
        # the same burst is less surprising for an entity with a high baseline
        self.assertGreater(momentum(4, 0.0), momentum(4, 3.0))

    def test_verification_rule(self):
        self.assertTrue(is_verified(make_item("a", tiers=("primary",)), 2))
        self.assertTrue(is_verified(make_item("b", tiers=("press", "trade")), 2))
        self.assertFalse(is_verified(make_item("c", tiers=("press", "social")), 2))
        self.assertFalse(is_verified(make_item("d", tiers=("press",)), 2))


class Placement(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.archive = Archive(self.repo.cfg)

    def tearDown(self):
        self.repo.cleanup()

    def test_total_matches_weighted_sum(self):
        it = make_item("x", impact=0.8, novelty=0.4)
        sc = score_item(self.repo.cfg, self.archive, it, date(2026, 9, 29))
        expected = 100 * sum(self.repo.cfg.weights[k] * sc["components"][k] for k in sc["components"])
        self.assertAlmostEqual(sc["total"], expected, delta=0.06)  # components are rounded to 3 dp

    def test_unverified_items_never_lead(self):
        items = [make_item("hot-rumour", tiers=("social",), impact=1.0, novelty=1.0, coverage=9)]
        items += [make_item(f"s{i}", impact=0.2 + 0.05 * i, entity=f"E{i}") for i in range(8)]
        ranked = score_and_place(self.repo.cfg, self.archive, items, date(2026, 9, 29))
        leads = [it["id"] for it in ranked if it["placement"] == "lead"]
        self.assertEqual(len(leads), self.repo.cfg.leads)
        self.assertNotIn("hot-rumour", leads)
        self.assertEqual(ranked[self.repo.cfg.leads]["id"], "hot-rumour")  # first brief
        self.assertEqual([it["rank"] for it in ranked], list(range(1, len(items) + 1)))

    def test_lead_diversity_cap(self):
        items = [make_item(f"big{i}", entity="MegaLab", impact=0.9) for i in range(4)]
        items += [make_item(f"other{i}", entity=f"Lab{i}", impact=0.3) for i in range(6)]
        ranked = score_and_place(self.repo.cfg, self.archive, items, date(2026, 9, 29))
        leads = [it for it in ranked if it["placement"] == "lead"]
        self.assertEqual(sum(it["entities"][0] == "MegaLab" for it in leads), self.repo.cfg.max_leads_per_entity)
        self.assertEqual(len(leads), self.repo.cfg.leads)

    def test_momentum_uses_archive_baseline(self):
        prior = [make_item(f"p{i}", entity="BusyLab", published_at="2026-09-28T10:00:00Z") for i in range(4)]
        self.repo.put_daily(make_issue("2026-09-28", prior))
        archive = Archive(self.repo.cfg)
        busy = score_item(self.repo.cfg, archive, make_item("n1", entity="BusyLab"), date(2026, 9, 29))
        quiet = score_item(self.repo.cfg, archive, make_item("n2", entity="QuietLab"), date(2026, 9, 29))
        self.assertAlmostEqual(busy["baseline"], 4.0)
        self.assertEqual(quiet["baseline"], 0.0)
        self.assertLess(busy["components"]["momentum"], quiet["components"]["momentum"])

    def test_momentum_counts_same_day_burst(self):
        cohort = [make_item(f"b{i}", entity="BurstLab") for i in range(4)] + [make_item("solo", entity="SoloLab")]
        placed = score_and_place(self.repo.cfg, self.archive, cohort, date(2026, 9, 29))
        by_id = {it["id"]: it["score"] for it in placed}
        self.assertEqual(by_id["b0"]["mentions"], 4)
        self.assertEqual(by_id["solo"]["mentions"], 1)
        self.assertGreater(by_id["b0"]["components"]["momentum"], by_id["solo"]["components"]["momentum"])


if __name__ == "__main__":
    unittest.main()
