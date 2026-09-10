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

## Open items / not yet done

- Finding #1 above (xpath fallback never fires for click targets, only
  form-control roles) is still open -- worth deciding whether it's a Week
  2 self-healing item or a smaller standalone fix.
- No formal before/after cost/latency metrics yet (Week 4 per the plan).
- Second target app has only been exercised for one flow (package lookup);
  the "held package" (403) and "not found" cases aren't yet discovered/replayed.