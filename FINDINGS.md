# Findings Log

Working notes captured while extending this project, kept close to when
each finding happened rather than reconstructed later. This is raw
material for the final README/write-up (Week 4), not the polished
version -- exact numbers, exact evidence paths, exact before/after.

---

## Day 5-6: Second target app (target_app_v2) and a real locator bug

**Motivation:** `target_app` proves the pipeline works against one legacy
app. To claim "generalizes," a second target with genuinely different
markup was needed -- specifically, one where role-based accessibility
queries mostly fail, forcing real reliance on the text/xpath fallback
tiers instead of just having them exist in code.

**Built:** `target_app_v2/` -- a package-tracking app (deliberately a
different domain than banking, so it doesn't read as a reskin) with:
- Login inputs with no `name`/`label`/`aria-label` (just a nearby `<span>`)
- "Buttons" that are `<div onclick=...>` with no button role at all
- No table layout (div-based), to rule out success being table-structure-dependent

**Finding #1 -- discovery-time gap (from `target_app`, not v2):**
Diagnosing the two original hard-failure runs
(`replay-20260813T205318-c74a8c`, `replay-20260813T205949-591450`) showed
discovery had *never* recorded an xpath/css fallback for any step across
the whole original evidence set -- only role/text. Root cause traced to
`agent/discovery.py`'s `_locate()`: the xpath fallback only fires for
`textbox`/`combobox`/`searchbox` roles, not for click targets. Not fixed
yet as of this writing -- documented as a known gap, candidate for a
future day.

**FIXED (Day 7):** added a click-target xpath fallback, mirroring the
textbox one -- "nearest following input" becomes "nearest following
clickable-looking element" (`button`, `a`, `@onclick`,
`role=button`/`link`). Tried *before* plain text matching for
`button`/`link` roles specifically, because otherwise a label element
that happens to text-match would short-circuit the fallback before it's
reached (e.g. `<span>View Record</span>` followed by a separate
icon-only `<div onclick=...>` -- the span "matches" but isn't clickable
in any useful way). Regression tests:
`tests/test_discovery_locate.py::test_locate_click_target_finds_sibling_element_not_its_label`
and `::test_locate_click_target_still_resolves_itself_when_self_labeled`
(the second one guards against the new fallback breaking the Finding #5
fix). Test suite: 14 -> 16 passing.

**Finding #2 -- replay engine crash on `target=None`:**
One artifact (`cap_a4783f4d47`) had a `fill` step with `target: null`
recorded by an earlier discovery run. Replaying it crashed with
`AttributeError: 'NoneType' object has no attribute 'strategies'` instead
of a clear error.
Fixed in `replay/engine.py`'s `_resolve()`: raises `LookupError` with an
actionable message when `locator is None`, instead of crashing.
Regression test: `tests/test_replay.py::test_resolve_raises_lookup_error_on_none_target`.
Note: this exact `target=None` case appears to predate current
`discovery.py` validation and wasn't reproduced fresh -- the fix hardens
against the *symptom* (crash) regardless of root cause, which is the
right fix either way.

**Finding #3 -- guardrails allowlist hardcoded to `target_app`'s routes:**
`run_agent.py` always constructed `AllowlistConfig()` with defaults
(`/login`, `/members`, `/logout`), so any discovery run against
`target_app_v2`'s `/packages/*` routes was rejected before it could even
start.
Fixed: added `--allowed-route-prefix` (repeatable) CLI flag to
`run_agent.py`, additive to the defaults, so each target app's routes
don't need to be hardcoded into the tool.

**Finding #4 -- `login_session.py` only supported one markup style:**
Hardcoded to `input[name=username]` / `input[name=password]` /
`role=button "Sign In"`, none of which exist (as *visible, fillable*
elements) on `target_app_v2`'s login page.
Fixed: added a visibility-checked fallback path (`#userfield`/`#passfield`
by id, "Sign In" by exact text) so one script bootstraps a session
against either target.

**Finding #5 -- the real one: unsafe substring text matching (this is
the interesting result of the whole exercise):**

First discovery run against `target_app_v2`
(`discovery-20260909T235011-3c5c35`) *succeeded* (7 steps), but not
cleanly: it clicked "Search" twice with no effect, then gave up and
navigated directly via URL to complete the goal.

Root cause, confirmed directly against a live page:
`page.get_by_text("Search", exact=False).first` matched **two** elements
on the search page -- `<b>Package Search</b>` (an inert header) and the
real `<div class="fakebtn">Search</div>` (the actual button) -- and
`.first` picked the header, because it comes first in DOM order and
"Search" is a substring of "Package Search". The click executed with no
error and had zero effect; nothing in the stack surfaces this, since
Playwright doesn't raise just because a click lands on a real (but wrong)
element.

Fixed in `agent/discovery.py`'s `_locate()`: text-matching now tries an
**exact** match first, falling back to substring matching only if no
exact match exists. Regression test:
`tests/test_discovery_locate.py` (2 tests: exact-preferred-over-substring,
and substring-still-works-as-fallback-when-nothing-exact-exists).

**Before/after, same goal, same app, only the fix changed:**

| | Before fix (`discovery-20260909T235011`) | After fix (`discovery-20260910T005308`) |
|---|---|---|
| Steps | 7 | 6 |
| "Search" click | Failed silently twice, then recovered via direct URL navigation | Succeeded on the first attempt |
| Outcome | Success (via workaround) | Success (via the intended UI flow) |

This is the single most useful piece of evidence generated so far: it's
a real bug, found by design (deliberately hostile markup), root-caused
to an exact line of code, fixed, and covered by a regression test that
would catch a regression of the same shape on a different page. Test
suite: 10 -> 14 passing tests across today's three fixes combined.

---

## Day 7: Broader outcome coverage on target_app_v2 + Finding #1 closed

Three more discovery runs against `target_app_v2`, rounding out the
outcome-type coverage to match what `target_app` already demonstrated:

- `discovery-20260910T055831-5f2117` -- **not found** case (`PK-0000`).
  4 steps, correctly recognized the "no shipment record" message as a
  legitimate outcome rather than a failure.
- `discovery-20260910T060031-c83883` -- **restricted/held** case
  (`PK-9000`, real HTTP 403 from the server). 5 steps, correctly reported
  the 403 message as an access restriction instead of erroring out or
  hallucinating shipment details.
- Plus the earlier `lookup_package_status` (`PK-1001`) success case from
  Day 6.

`target_app_v2` now has real evidence for all three outcome classes
(success / business outcome / restricted) on a structurally different
app than the original, which is the actual generalization claim this
whole exercise was meant to test.

**Finding #6 -- the same bug, fixed in one file, still broken in its
sibling (the most instructive result of this whole exercise):**

After adding outcome coverage above, all three `target_app_v2` artifacts
were replayed deterministically (no LLM) for the first time -- and all
three hard-failed, 100%. Root cause: `replay/engine.py`'s `_resolve()`
had its own, separate `get_by_text(..., exact=False)` text-matching
logic -- the exact same substring-ambiguity bug fixed in Finding #5,
except that fix only touched `agent/discovery.py`'s `_locate()`. The two
functions look similar (this file's own docstring already claimed they
"mirror" each other) but are independent implementations, and fixing one
silently left the other broken. Concretely: `_resolve`'s text strategy for
"Search" matched `<b>Package Search</b>` (the inert header) before the
real button, for the identical DOM-order-plus-substring reason as
Finding #5 -- so the click had no effect, and every step after it failed
too, since the page never left the search form.

Fixed: applied the same exact-then-substring fix to `_resolve()`.
Regression test: `tests/test_replay.py::test_resolve_text_strategy_prefers_exact_match_over_substring`.
Test suite: 16 -> 17 passing.

**Before/after, same three artifacts, only the fix changed:**

| Artifact | Before fix | After fix |
|---|---|---|
| `cap_53043bc026` (lookup_package_status, PK-1001) | `hard_failure` at step s01/s03 (see below) | `success`, outputs match discovery exactly |
| `cap_a9447b47e3` (lookup_nonexistent_package, PK-0000) | `hard_failure` (same root cause) | `success` |
| `cap_a65b404837` (lookup_held_package, PK-9000) | `hard_failure` (same root cause) | `success`, correctly reports the hold message as `hold_status_message` |

(Note: the very first re-run after the fix still failed once, at step
s01 -- but that was a stale/expired session cookie, ~10+ minutes old,
not a code issue. Re-running `login_session.py` immediately before
replay resolved it, confirming the session-timeout behavior is working
as designed, not masking a real bug.)

**Why this is the most important finding in the project:** it's not
"a bug was found and fixed" -- that's Findings #2-5 too. It's "a fix
applied in one place did not automatically apply to a structurally
similar but independent implementation elsewhere," which is a very real
and common failure mode in codebases with duplicated logic (discovery's
live execution and replay's deterministic execution necessarily
duplicate some locator logic, since one drives an LLM loop and the other
doesn't). The lesson that matters for this project's actual thesis --
"deterministic replay reproduces what discovery found, with zero
inference cost" -- is that this guarantee needed to be *tested*, not
assumed from discovery working. It wasn't actually true until today.

## Open items / not yet done

- **Self-healing re-discovery (deferred, not forgotten):** the original
  plan's Week 2 called for an LLM-assisted pass that proposes a new
  locator automatically when replay hits a hard failure, using a DOM
  snapshot at failure time. `dashboard/diagnosis.py` covers the
  *explain-why* half of this; the *propose-a-fix* half is intentionally
  deferred. Reasoning: the project's current core claim -- deterministic,
  zero-LLM-cost replay -- is fully proven and well-evidenced as of Day 7;
  reintroducing an LLM call into the replay path is a second, different
  thesis that deserves its own clean scoping (does it patch the artifact
  in place? version it? retry just the one step? what if the LLM also
  can't find the element?) rather than being rushed in on top of an
  already-complete story. Picking this up as a deliberate second phase,
  after metrics/README, rather than mid-stream.
- **DONE:** cost and reliability metrics formalized as reproducible
  scripts (`dashboard/cost_estimate.py`, `dashboard/reliability.py`),
  both derived from real evidence, not invented numbers:
  - Cost: ~$0.07/run in LLM spend avoided per replay (typical case,
    Sonnet 4.6 public pricing, 7 real completed discovery runs, 5.1
    average LLM calls/run)
  - Reliability: 29% -> 100% replay success rate after fixing Findings
    #2/#5/#6, with one non-code (stale session) failure explicitly
    excluded and disclosed rather than folded into either number