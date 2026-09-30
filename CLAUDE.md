# AI Daily: notes for Claude

* This repo is a daily AI-news pipeline. When running the daily routine, follow **RUNBOOK.md** step by step.
* Python 3.11+, standard library only. Tests: `python3 -m unittest discover -s tests`. Full check: `python3 -m ainews check`.
* `issues/**` and `artifacts/latest.html` are generated. Never hand-edit them, or the `score`/`placement`/`rank` fields
  in `data/`. Change the research JSON or the code, then rebuild.
* Never invent facts, numbers or URLs in `data/`. Unverified details go in `confidence_note`.
* Scoring constants live in `config.toml`; the maths is documented in METHODOLOGY.md. Keep the two in sync.
* The dashboard template (`ainews/templates/dashboard.html`) is an Artifact page: no doctype/html/head/body tags, theme
  tokens on `:root` with dark overrides, no external requests except Google Fonts.
* `ainews/harvest.py` and the price fetch in `ainews/market.py` need the internet: they run in GitHub Actions
  (`.github/workflows/harvest.yml`), not in the routine's sandbox. Everything else must stay network-free.
