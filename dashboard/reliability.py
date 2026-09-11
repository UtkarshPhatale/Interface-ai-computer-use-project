"""
Reliability metric: replay success rate before vs. after the locator-
matching fixes (Findings #1, #2, #5, #6 in FINDINGS.md).

Each real replay run is hand-classified below rather than inferred purely
from timestamps, because timestamp alone isn't reliable evidence of "which
code was running" -- e.g. replay-20260910T063714-390d0d has a post-fix-era
timestamp but actually failed on a STALE SESSION COOKIE (an operational/
environment issue -- the session simply expired, same as any auth system),
not a locator bug at all. It's excluded from this comparison entirely,
same principle as dashboard/report.py excluding never-started runs from
its success-rate denominator: only count what the metric is actually
meant to measure.

If a new replay run is added later, add its classification here rather
than trying to make this fully automatic -- being explicit about *why*
each run is bucketed where it is is more honest than a heuristic that
might misclassify a future edge case the same way timestamp-only bucketing
almost did here.
"""

# (run_id, outcome, bucket, note)
# bucket: "pre_fix_code_bug" | "post_fix" | "excluded_non_code"
RUN_CLASSIFICATIONS = [
    ("replay-20260813T205318-c74a8c", "hard_failure", "pre_fix_code_bug",
     "Finding #1-adjacent: role+text tried, no css/xpath fallback recorded"),
    ("replay-20260813T205949-591450", "hard_failure", "pre_fix_code_bug",
     "Finding #2: target=None crashed the old _resolve()"),
    ("replay-20260813T215714-a12ccb", "success", "pre_fix_code_bug",
     "baseline success, included for a fair pre-fix denominator"),
    ("replay-20260813T215805-85204e", "business_outcome", "pre_fix_code_bug",
     "baseline business outcome, included for a fair pre-fix denominator"),
    ("replay-20260910T062047-5085fa", "hard_failure", "pre_fix_code_bug",
     "Finding #6: substring text-match bug in replay/engine.py._resolve()"),
    ("replay-20260910T062103-9a03a3", "hard_failure", "pre_fix_code_bug",
     "Finding #6: same substring text-match bug"),
    ("replay-20260910T062114-8097f8", "hard_failure", "pre_fix_code_bug",
     "Finding #6: same substring text-match bug"),
    ("replay-20260910T063714-390d0d", "hard_failure", "excluded_non_code",
     "Stale/expired session cookie (~10+ min old), not a locator or code "
     "defect -- confirmed by an immediate re-run succeeding after re-login"),
    ("replay-20260910T063758-b80a3c", "success", "post_fix",
     "First clean run after the Finding #6 fix, fresh session"),
    ("replay-20260910T063806-965c98", "success", "post_fix",
     "Fresh session, post-fix"),
    ("replay-20260910T063814-059b49", "success", "post_fix",
     "Fresh session, post-fix"),
]


def _rate(rows):
    total = len(rows)
    successes = sum(1 for _, outcome, _, _ in rows if outcome in ("success", "business_outcome"))
    return successes, total, (successes / total * 100 if total else 0.0)


def main() -> None:
    pre_fix = [r for r in RUN_CLASSIFICATIONS if r[2] == "pre_fix_code_bug"]
    post_fix = [r for r in RUN_CLASSIFICATIONS if r[2] == "post_fix"]
    excluded = [r for r in RUN_CLASSIFICATIONS if r[2] == "excluded_non_code"]

    s, t, r = _rate(pre_fix)
    print(f"Pre-fix (Findings #1/#2/#6 present as live bugs): {s}/{t} succeeded = {r:.0f}%")
    s2, t2, r2 = _rate(post_fix)
    print(f"Post-fix (current code):                          {s2}/{t2} succeeded = {r2:.0f}%")

    if excluded:
        print(f"\nExcluded ({len(excluded)}, not a code defect):")
        for run_id, outcome, _, note in excluded:
            print(f"  {run_id} ({outcome}) -- {note}")

    print(
        f"\nHeadline: replay success rate went from {r:.0f}% to {r2:.0f}% "
        f"after fixing the substring text-matching bug (Findings #5/#6) and "
        f"the None-target crash (Finding #2) -- same three real capability "
        f"artifacts, same target apps, only the matching logic changed."
    )


if __name__ == "__main__":
    main()