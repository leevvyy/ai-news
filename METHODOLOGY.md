# Methodology

How a candidate story becomes a lead, a brief or an "also noted" line. All of this is implemented in
[`ainews/scoring.py`](ainews/scoring.py), [`ainews/dedupe.py`](ainews/dedupe.py) and
[`ainews/timewin.py`](ainews/timewin.py); every constant lives in [`config.toml`](config.toml).

## 1. Importance score

$$
I = 100\sum_{k} w_k\, s_k, \qquad s_k \in [0,1], \qquad \sum_k w_k = 1
$$

| $k$ | $w_k$ | Measures | Source |
|---|--:|---|---|
| impact | 0.35 | who or what changes (users, $, state of the art) | rubric, set during research |
| novelty | 0.20 | first of its kind vs incremental | rubric, set during research |
| credibility | 0.20 | primary source > press > trade > social | computed from `sources` |
| breadth | 0.15 | independent outlets covering the story | computed from `coverage` |
| momentum | 0.10 | today's stories on the entity vs its 7-day baseline | computed from the archive |

### Computed components

**Credibility** is a noisy-OR over the $n=3$ most reliable distinct outlet domains $j$:

$$
s_{\text{cred}} = 1 - \prod_{j \le n} \bigl(1 - r_{\text{tier}(j)}\bigr),
\qquad r = \{\text{primary } 0.60,\ \text{press } 0.45,\ \text{trade } 0.25,\ \text{social } 0.10\}
$$

It reads as the probability that at least one of the best sources is reliable. Several links on one domain count
once, at that domain's best tier.

**Breadth** saturates in the number of independent outlets $c$:

$$
s_{\text{breadth}} = 1 - e^{-c/\kappa}, \qquad \kappa = 6
$$

**Momentum** is an attention burst relative to the primary entity's own baseline:

$$
s_{\text{mom}} = 1 - \exp\!\left(-\frac{m_t}{\mu_7 + 1}\right)
$$

where $m_t$ is the number of stories in today's issue on the entity and $\mu_7$ is its mean stories per day over
the previous 7 archived days. Days missing from the archive are skipped rather than counted as zero, so a young
archive does not inflate momentum forever.

### Rubrics

| $s$ | Impact | Novelty |
|--:|---|---|
| 1.0 | shifts the frontier or industry; >100M users or >$10B | first of its kind |
| 0.8 | major release or deal from a top lab or company | |
| 0.6 | significant and sector-relevant | a meaningful step |
| 0.4 | notable but niche | |
| 0.2 | minor | incremental or expected (0.3) |

### Calibration (inaugural issue, n = 28)

The first draft used a noisy-OR over *all* outlets with $r=\{.9,.7,.5,.2\}$, $\kappa=3$, and coverage as $m_t$.
Three of the five components were then almost constant, so 45% of the weight handed out the same ~45 points to
every story:

| component | before: mean | before: sd | after: mean | after: sd |
|---|--:|--:|--:|--:|
| credibility | 0.968 | 0.077 | 0.813 | 0.114 |
| breadth | 0.800 | 0.186 | 0.597 | 0.194 |
| momentum | 0.955 | 0.111 | 0.826 | 0.161 |
| $I$ | 69.4 | 11.3 | 61.9 | 12.2 |

Momentum is still high on day one because $\mu_7 \approx 0$ for every entity; it calibrates as the archive grows.
Re-run [`scripts/calibration.py`](scripts/calibration.py) after a few weeks to check the spread again.

## 2. Placement

1. Sort by $(-I,\ -s_{\text{cred}},\ \text{id})$.
2. **Leads**: the first 5 *verified* items, with at most 2 leads sharing a primary entity (a diversity constraint,
   so one company's big day cannot fill the page). Verified means ≥ 1 primary source, or ≥ 2 distinct non-social outlets.
3. **Briefs**: the next 10 items (unverified ones are flagged).
4. **Also noted**: the rest. They stay in the archive and feed the weekly pool.

## 3. Window

Let $t_e$ be the run time and $t_p$ the previous issue's window end:

$$
t_s = \begin{cases}
t_p & 0 < t_e - t_p \le 72\,\text{h} \\
t_e - 72\,\text{h} & t_e - t_p > 72\,\text{h}\ \text{(missed runs, flagged as a gap)} \\
t_e - 24\,\text{h} & \text{no previous issue}
\end{cases}
$$

The issue is dated by the UTC+8 calendar date of the midpoint $(t_s+t_e)/2$, which is "yesterday" for the
06:00 run. Consecutive windows tile time with no gaps and no overlaps. Items up to 6 h before $t_s$ are accepted with
a warning (stories that kept developing).

## 4. Dedupe

Against the previous 7 days (dailies plus any weekly back-fill), two items are the same story if they share a
canonical source URL (host case, `www.`, tracking parameters, fragment and trailing slash removed) or if

$$
J(A,B) = \frac{|A \cap B|}{|A \cup B|} \ge 0.5
$$

on title token sets **and** they share a canonical entity. Genuine developments carry `follow_up_of`.

## 5. Aggregates

* **This week so far** (daily, minor): the 7 days ending on the issue date. It shows the top 5 stories that are not
  today's leads, the topic mix, and the entities in play.
* **Entities in play / leaderboard**: $\sum I$ attributed 1.0 to each story's primary entity and 0.25 to every other
  entity it names.
* **Calendar**: `upcoming` events from the last 30 days that are still ahead, merged when the date matches and title
  $J \ge 0.6$. Later mentions update earlier ones.
* **Weekly recap** (Mondays): ISO week, Monday to Sunday UTC+8, with themes written by the researcher and statistics
  computed from the dailies.
