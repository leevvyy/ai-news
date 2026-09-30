"""Rolling news window: previous run's end → now, capped, dated by its midpoint.

Let t_e be the run time and t_p the previous issue's window end. Then

    t_s = t_p                      if 0 < t_e - t_p <= H_max
        = t_e - H_max              if t_e - t_p >  H_max   (missed runs; flagged as a gap)
        = t_e - 24h                if no previous issue

and the issue date is the reader-local calendar date of the midpoint
(t_s + t_e) / 2. For the scheduled 06:00 UTC+8 run that is "yesterday", and a
72 h catch-up window is dated by its middle day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .archive import Archive, fmt_ts, parse_ts, week_id
from .config import Config


@dataclass(frozen=True, slots=True)
class Window:
    start: datetime
    end: datetime
    issue_date: date
    gap: bool                  # True when runs were missed and the window was capped
    previous_end: datetime | None

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    @property
    def closes_week(self) -> bool:
        """Sunday issue ⇒ the Monday run also produces the weekly recap."""
        return self.issue_date.isoweekday() == 7

    @property
    def week(self) -> str:
        return week_id(self.issue_date)

    def as_json(self) -> dict[str, str]:
        return {"start": fmt_ts(self.start), "end": fmt_ts(self.end)}


def compute_window(cfg: Config, archive: Archive, now: datetime | None = None) -> Window:
    end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
    cap = timedelta(hours=cfg.max_window_hours)

    prev_end: datetime | None = None
    for d in reversed(archive.daily_dates()):
        iss = archive.daily(d)
        if iss and "window" in iss:
            candidate = parse_ts(iss["window"]["end"])
            if candidate < end:
                prev_end = candidate
                break

    gap = False
    if prev_end is None:
        start = end - timedelta(hours=24)
    elif end - prev_end > cap:
        start, gap = end - cap, True
    else:
        start = prev_end

    midpoint = start + (end - start) / 2
    return Window(start, end, midpoint.astimezone(cfg.tz).date(), gap, prev_end)


def in_window(ts: str, win_start: str, win_end: str, grace_hours: int) -> tuple[bool, bool]:
    """(inside, inside_with_grace) for an item's published_at."""
    t, s, e = parse_ts(ts), parse_ts(win_start), parse_ts(win_end)
    return s <= t <= e, s - timedelta(hours=grace_hours) <= t <= e
