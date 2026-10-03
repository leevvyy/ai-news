# RUNBOOK: the daily AI Daily routine

The **AI Daily** routine fires every day at **21:48 UTC (05:48 UTC+8)** in a fresh cloud session and follows this file
from top to bottom. The goal: by about 06:15 UTC+8 there is a scored issue on `main`, a refreshed dashboard at the
living artifact URL, and a push notification.

Hard rules (apply throughout):

1. **Never invent** a story, number, quote, date or URL. Every fact must come from a page or search result you saw.
   If you only saw a snippet, say so in `confidence_note`.
2. **Never hand-edit generated files** (`issues/**/*.md`, `artifacts/latest.html`) or the `score` / `placement` /
   `rank` fields in `data/`. `python3 -m ainews check` (and CI) rebuild and diff them.
3. English output. Chinese-language sources are collected, translated, and keep their headline in `original_title`.
4. Commit only to `main` in `leevvyy/ai-news`. Touch nothing outside `data/`, `issues/`, `artifacts/`.

---

## 0 · Set up (≈2 min)

```bash
git clone https://github.com/leevvyy/ai-news.git && cd ai-news     # skip if already inside it
git checkout main && git pull --ff-only
test -f RUNBOOK.md -a -d ainews || { echo "scaffolding missing on main"; exit 1; }
python3 -m unittest discover -s tests -q          # must pass
python3 -m ainews window                          # read the JSON it prints
python3 -m ainews new                             # writes data/daily/<issue_date>.json skeleton
python3 -m ainews raw > /tmp/harvest.txt          # digest of tonight's harvest (read it; share with the desks)
```

`window` prints `issue_date`, the UTC and UTC+8 window, `recent_items` (the last 7 days, **do not repeat these**),
`weekly_due` (non-null on Mondays), `open_calendar` (upcoming events already tracked) and `raw_file`.

`raw` digests the newest nightly harvest (GitHub Action at 01:37 and 04:37 UTC+8, often hours late, `data/raw/<date>.json`): multi-outlet **story
clusters** (EN and 中文) ranked by the number of distinct outlets, primary-source posts, Hugging Face papers by upvotes
and Hacker News stories by points, each with exact timestamps and URLs. `news.google.com/rss/articles/…` links are
Google News redirects: never cite them; search the headline and cite the outlet's own URL. Feed summaries are the outlets' own text, so
they count as "seen" sources. If `raw` says there is no harvest (the Action failed), research with WebSearch only
and add a line to `notes`. If it prints a `STALE:` line, the harvest covers only the start of the window: use it for
that span and cover the rest (harvested_at → window end) with WebSearch.

* If the clone or push is refused, call `add_repo(owner="leevvyy", repo="ai-news", access="push")` and retry.
* If `RUNBOOK.md`/`ainews/` are missing on `main`, stop and notify: "AI Daily: scaffolding PR not merged yet".
* If `new` reports the file already exists, an earlier run got that far. Inspect it; if it has items, go to step 4.

## 1 · Budget

* **WebSearch: ~200 calls per session, shared with subagents.** Plan about 150 for the daily issue, 25 for the
  Monday weekly and 15 for the calendar. Use date-explicit queries ("September 30 2026 OpenAI", "9月30日 大模型 发布").
* **WebFetch is blocked for many news domains** by the environment's network policy. `github.com`, `arxiv.org` and some
  lab sites usually work. **Start from the harvest**, which already has the text of most feeds, and use WebSearch to
  confirm, find primary sources and fill gaps. When neither reaches a page, verify across ≥ 2 independent search
  results and note it.
* Run the research as **three parallel subagents** (general-purpose, ≤ 50 searches each), one per desk in §2. Give
  each the window, `recent_items`, the item schema in §3 and the no-invention rule. Ask for JSON back, then merge.

## 2 · Research desks

Only stories first published inside the window (up to 6 h earlier is accepted with a warning, e.g. when a story kept
developing). Target **20–30 candidates** in total. A quiet day with 12 good items beats 30 padded ones.

Seed each desk with its slice of the harvest digest. Clusters with `n_outlets ≥ 3` are almost always stories. Use a
cluster's outlet count for `coverage` when it exceeds what you saw yourself. Make sure the China desk returns at least
**3** solid 中文 stories: the build guarantees ≥ 2 China items among leads + briefs and swaps them in if needed.

| Desk | Beat | Where to look (see `sources.toml`) |
|---|---|---|
| A · Labs & research | frontier/open-weight model releases, pricing/API changes, benchmarks, notable papers (1–2 line technical takeaway), products, agents, dev tools | lab blogs/newsrooms, GitHub releases, Hugging Face (models, papers), arXiv, The Verge, TechCrunch, Ars, VentureBeat, Simon Willison, HN (buzz only) |
| B · Business, compute & policy | funding/M&A/earnings/IPOs, datacenters, power, chips & memory (NVDA, AMD, TSMC, Samsung, SK hynix, Micron, Kioxia, Broadcom), export controls, US/EU/UK/China regulation, lawsuits, safety incidents | Reuters, Bloomberg, FT, WSJ, CNBC, The Information, Axios, Nikkei Asia, DigiTimes, SemiAnalysis, whitehouse.gov, commerce.gov, europa.eu |
| C · 中文 China desk | DeepSeek, Qwen, Kimi, Zhipu, MiniMax, ByteDance Seed/豆包, Tencent 混元, Baidu, StepFun, Kuaishou 可灵, Huawei Ascend, Cambricon, SMIC, CXMT, CAC/MIIT policy, HK/STAR AI listings | 机器之心, 量子位, 新智元, 36氪, 财新, 第一财经, 界面, 澎湃, IT之家, official lab posts/GitHub/HF; SCMP, Caixin Global |

Also collect **upcoming** events (launches with dates, conferences, earnings of AI-relevant companies, regulatory
deadlines, awaited results) for the calendar.

Before writing, **dedupe**: drop anything already in `recent_items`. If a story genuinely moved on (new numbers, a
decision, a launch after an announcement), keep it and set `"follow_up_of": "<earlier id>"`.

## 3 · Write `data/daily/<issue_date>.json`

Keep the `date` and `window` that `new` wrote. Fill `items`, `upcoming`, optional `notes`; leave `tldr` for step 4.

```jsonc
{
  "id": "openai-gpt-6-1-sol-devday",            // kebab-case, unique, stable
  "title": "Factual English headline",          // ≤ 110 chars, no hype
  "original_title": "中文原标题",                 // only for Chinese-sourced items
  "summary": "2–4 sentences: what happened, with the concrete specifics.",
  "why_it_matters": "1–2 sentences: second-order effect, who it affects, market read-through.",
  "key_numbers": ["$2/M input tokens", "+2.2 pts AutomationBench"],   // ≤ 6, the ones that matter
  "topic": "models | research | products | business | compute | policy",
  "region": "us | cn | eu | global | other",
  "entities": ["OpenAI", "Microsoft"],          // PRIMARY ENTITY FIRST; canonical names (config.toml aliases)
  "tickers": ["MSFT"],                          // listed companies that are a PARTY to the story, [] if none
  "published_at": "2026-09-29T17:00:00Z",       // first report, UTC; estimate → say so in confidence_note
  "sources": [ {"url": "https://…", "outlet": "OpenAI", "lang": "en|zh|other",
                "tier": "primary|press|trade|social", "title": "headline at the source"} ],
  "coverage": 6,                                // independent outlets you saw covering it (≥ distinct domains)
  "impact": 0.8, "novelty": 0.6,                // rubric below
  "follow_up_of": null, "confidence_note": null
}
```

* **Tiers**: primary = the organisation's own channel (blog, paper, model card, filing, government doc, official
  GitHub/X account); press = established newsroom; trade = specialist media/newsletters; social = X/Reddit/HN/Weibo/Zhihu.
  A lead needs ≥ 1 primary source **or** ≥ 2 distinct non-social outlets; otherwise the build keeps it out of the leads.
* A roundup or live-blog URL may support **one** item only (the dedupe gate rejects shared URLs).
* **Tickers** feed the market event study, so tag only the companies that announced, acquired, were acquired, reported
  or were directly regulated. Don't tag partners, investors or customers "for context": MSFT on every OpenAI story would
  count one Microsoft price move many times. Use Yahoo symbols (`NVDA`, `2330.TW`, `005930.KS`, `285A.T`, `9988.HK`,
  `688256.SS`).
* **Impact** (0–1): 1.0 shifts the frontier or industry (>100M users, >$10B); 0.8 major release or deal from a top
  player; 0.6 significant and sector-relevant; 0.4 notable but niche; 0.2 minor.
  **Novelty**: 1.0 first of its kind; 0.6 a meaningful step; 0.3 incremental or expected; 0.1 rehash.
  Score the story, not the company: an incremental OpenAI update is not a 0.8.
* `upcoming` entries: `{"date": "YYYY-MM-DD" or "YYYY-MM", "title", "kind": launch|conference|earnings|policy|deadline|result|other,
  "confidence": confirmed|expected|rumored, "url"}`. Events already in `open_calendar` carry forward automatically;
  add only new ones or ones whose date or status changed.

## 4 · Build, then write the TL;DR

```bash
python3 -m ainews build          # validate → dedupe → score → place → Markdown + dashboard
```

Errors block the build: fix the JSON and rerun. Read the warnings too. Then write `tldr`: **3 bullets**, each 1–2
sentences, covering the five leads (and one China/Asia line when there is a notable one), with the key number in each.
Rebuild, then:

```bash
python3 -m ainews check          # must end with "OK"
```

## 5 · Mondays only (`weekly_due` is set): weekly recap

Write `data/weekly/<week>.json`:

```json
{"week": "2026-W40", "headline": "One sentence on the week.",
 "themes": [{"title": "≤ 60 chars", "summary": "2–3 sentences", "item_ids": ["ids from that week's dailies"]}],
 "upcoming": [], "notes": ""}
```

3–4 themes, each citing 2–4 item ids from that Monday–Sunday's daily files. (`items` is only for back-filling weeks
without daily issues.) Run `python3 -m ainews weekly <week>` and `python3 -m ainews check`.

## 6 · Commit and push

```bash
git add data issues artifacts
git commit -m "news: <issue_date>"            # Mondays: "news: <issue_date> + weekly <week>"
git push origin main
```

The push triggers CI, which rebuilds the site and deploys it to <https://leevvyy.github.io/ai-news/>. There is
nothing to publish by hand. If `check` fails in CI, the site is not deployed, so step 4's `check` must be green.

On a non-fast-forward rejection: `git pull --rebase origin main`, rerun `build` + `check`, push again. On a permission
refusal: `add_repo(..., access="push")`, then retry; as a last resort push the changed files with the GitHub MCP
`push_files` tool to `main`.

## 7 · Publish the living dashboard

Artifact tool: `action: "read"` with `url` = `[artifact].url` from `config.toml`, then
`action: "publish"`, `file_path: artifacts/latest.html`, the same `url`, `label: "Issue <issue_date>"`, no `icon`.

## 8 · Notify

Finish with a `<routine_summary>` whose body is the output of `python3 -m ainews summary`. Its first sentence is the
#1 headline, which becomes the push banner. On failure, the summary says what failed and at which step.
