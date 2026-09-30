#!/usr/bin/env python3
"""Score calibration report: is each component of I actually discriminating?

    python3 scripts/calibration.py            # all archived days
    python3 scripts/calibration.py --days 14  # the last 14 issues

A component with a tiny standard deviation is a near-constant and wastes its weight.
High pairwise correlation means two components measure the same thing twice.
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ainews.archive import Archive  # noqa: E402
from ainews.config import COMPONENTS, load_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=0, help="only the last N issues (default: all)")
    args = ap.parse_args()
    cfg = load_config()
    archive = Archive(cfg)
    dates = archive.daily_dates()[-args.days:] if args.days else archive.daily_dates()
    rows = [it["score"] for d in dates for it in (archive.daily(d) or {}).get("items", []) if "score" in it]
    if len(rows) < 3:
        print("not enough scored items yet")
        return 1

    total = [r["total"] for r in rows]
    var_i = st.pvariance(total)
    print(f"{len(rows)} items over {len(dates)} issue(s) · I mean {st.mean(total):.1f} · sd {st.pstdev(total):.1f}"
          f" · range {min(total):.1f}–{max(total):.1f}\n")
    print(f"{'component':<12}{'w':>6}{'mean':>8}{'sd':>8}{'Var(w·s)/Var(I)':>18}")
    cols = {k: [r["components"][k] for r in rows] for k in COMPONENTS}
    for k in COMPONENTS:
        share = st.pvariance([100 * cfg.weights[k] * x for x in cols[k]]) / var_i if var_i else 0.0
        flag = "  ← near-constant" if st.pstdev(cols[k]) < 0.08 else ""
        print(f"{k:<12}{cfg.weights[k]:>6.2f}{st.mean(cols[k]):>8.3f}{st.pstdev(cols[k]):>8.3f}{share:>17.1%}{flag}")

    print("\nPearson r between components")
    print(" " * 12 + "".join(f"{k[:6]:>8}" for k in COMPONENTS))
    for a in COMPONENTS:
        cells = []
        for b in COMPONENTS:
            try:
                cells.append(f"{st.correlation(cols[a], cols[b]):>8.2f}")
            except st.StatisticsError:  # a constant column
                cells.append(f"{'—':>8}")
        print(f"{a:<12}" + "".join(cells))
    redundant = [(a, b) for a, b in combinations(COMPONENTS, 2)
                 if st.pstdev(cols[a]) and st.pstdev(cols[b]) and abs(st.correlation(cols[a], cols[b])) > 0.8]
    if redundant:
        print("\nredundant pairs (|r| > 0.8): " + ", ".join(f"{a}/{b}" for a, b in redundant))
    return 0


if __name__ == "__main__":
    sys.exit(main())
