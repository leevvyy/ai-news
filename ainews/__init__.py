"""AI Daily: research JSON in, scored issue out (Markdown, living HTML dashboard, static site + Atom).

Pipeline (see RUNBOOK.md):

    window  ->  research (Claude)  ->  data/daily/<date>.json
            ->  validate -> dedupe -> score -> place
            ->  issues/daily/<date>.md, artifacts/latest.html, (Sundays) issues/weekly/<week>.md

Everything downstream of the research JSON is deterministic, so CI can rebuild
and diff every generated file.
"""

__version__ = "1.0.0"
