# REPORT

## 1. Architecture

Single process, synchronous, no queues or services — justified by the
assignment's explicit "don't reward scaling infrastructure" guidance. The
system has five components with one responsibility each:

- **Discovery agent** (`agent/discovery.py`): an LLM observe→decide→act loop
  driving a real Playwright browser. It perceives the page via its
  **accessibility tree** (role + accessible name), not raw DOM/HTML. This is
  the single most important mechanism decision: legacy enterprise apps have
  no test IDs and unstable markup, but the accessibility tree is available
  on any surface a human operator can use — including, per the glossary,
  desktop apps. Choosing it now means the perception layer doesn't have to
  be rebuilt to extend to a legacy web app or a native app later (see
  Section 4).
- **Artifact builder** (`artifact/builder.py`): converts a raw discovery
  trace into the final `CapabilityArtifact`. This is a deliberate seam — the
  LLM loop only has to accomplish the goal; parameterization, risk
  classification, and error-handling metadata are attached afterward. This
  keeps the artifact "decoupled from the raw model transcript" (the brief's
  own words) as a structural property, not just a description.
- **Replay engine** (`replay/engine.py`): executes a saved artifact with no
  LLM involved, using the same role/text/xpath locator semantics discovery
  used, so what worked during discovery is what replay tries first.
- **Guardrails** (`guardrails/policy.py`): allowlist + risk policy + redaction,
  applied identically in both discovery and replay.
- **Escalation** (`escalation/`): a file-backed intervention model plus a
  mock console — used by both discovery and replay when either gets stuck.

Trade-off: I chose a single relational-ish JSON artifact + JSONL evidence log
over a database. At this scale, a DB adds operational surface without
enabling any query the file layout doesn't already support, and JSON is
directly reviewable in a PR the same way the artifact itself is meant to be.

## 2. Artifact schema

`artifact/schema.py` defines `CapabilityArtifact`. Design priorities, in
order:

1. **A contract before a step list.** `CapabilityContract` (goal, input
   schema, output schema, preconditions) is what an AI agent reads to decide
   whether/how to call the capability — it should never need to parse steps
   to know what the capability needs or returns.
2. **Locators are ranked strategy lists, not single selectors.** Each
   `Locator` carries an ordered `list[LocatorStrategy]` — role+name first,
   text next, CSS/XPath last. Role+name is what survives a markup rewrite
   (it reflects what a human/screen-reader perceives); CSS/XPath is the most
   coupled to implementation detail and therefore the fallback of last
   resort. Replay tries each in order and would log which one worked (the
   telemetry hook for detecting drift, discussed in Section 4).
3. **Error handling is data, not code.** Each `Step` carries a list of
   `ExpectedOutcome`s: a `detect` locator, an `outcome_class`
   (business_outcome / recoverable / hard_failure), a `recovery` action, and
   a stable `result_code`. The replay engine has zero target-app-specific
   `if` statements — all of that knowledge lives in the artifact, which is
   exactly what makes it reviewable and portable.
4. **A checkpoint, not an assumption.** `success_checkpoint` is a distinct
   field the replay engine verifies *after* all steps run — "the last click
   didn't throw" is never treated as proof the goal was reached.

## 3. Determinism & error handling

Replay is deterministic in the sense the brief means it: no model decides
anything at replay time. Every decision — which locator strategy to try,
whether a runtime condition is a business outcome or a failure, what to do
about it — was fixed at artifact-authoring time.

Runtime robustness, layered:

- **Locator fallback** (role → text → css/xpath) absorbs small markup
  changes without needing to re-record.
- **Bounded retry** (3 attempts, 1s apart) on any step, for transient
  slowness — separate from business-outcome handling, because it's a
  generic behavior, not app-specific knowledge.
- **Expected-outcome matching**, checked both when a step's own execution
  throws *and* immediately after it succeeds (some business outcomes render
  as a normal 200 page, not an exception) — this is how "member not found"
  and "member restricted" are correctly classified as `business_outcome` in
  the demo (see `/evidence/`), rather than surfacing as crashes.
- **Recoverable conditions act, not just log.** `DISMISS_AND_CONTINUE`
  carries its own `recovery_locator` (e.g. the re-auth interstitial's
  "Continue" button) and re-drives the interrupted step after handling it.
- **Hard failures carry debug detail**: failed step id, what locator was
  expected, what exception was actually observed.

A real bug I hit and fixed during testing illustrates the "no clean DOM"
problem directly: a CSS `tr:has-text(...)` selector for the balance table
matched *all* rows, not just the intended one, because Playwright's
`:has-text` pseudo doesn't scope the way plain CSS does. I added XPath
(`LocatorKind.XPATH`) as a fourth strategy specifically for this
"row containing label X, read sibling cell" pattern common in legacy
table-based layouts — `contains(., 'Savings Balance')` (descendant text,
since the label sits inside a `<b>`) rather than `contains(text(), ...)`
(direct text only). This is exactly the kind of "interesting failure isn't
layout drift, it's a runtime/DOM-quirk condition" the brief describes.

## 4. Heterogeneity & multi-tenant

**Surface abstraction.** The seam is already in place: `agent/discovery.py`
perceives via role+name and produces `TraceStep`s in terms of role+name;
`replay/engine.py` consumes `Locator`/`LocatorStrategy` in the same terms.
Neither cares whether the underlying surface is Playwright-over-Chromium.
Extending to a *legacy* web app needs no new abstraction, just more locator
strategies in the fallback chain (this project already needed one — XPath).
Extending to a *desktop* app means swapping the perception/action
implementation (Playwright → an OS accessibility API / UIAutomation on
Windows or AXUIElement on macOS) behind the same `Locator` contract — role
and accessible name are first-class concepts in desktop accessibility APIs
too, so the schema doesn't change, only the executor that resolves it.

**Multi-tenant reuse.** `CapabilityArtifact.vendor_product` groups artifacts
by underlying vendor product rather than by tenant, and
`CapabilityArtifact.tenant_overrides: dict[tenant_id, ...]` is a reserved
override map for per-tenant patches (a locator that needs a different
accessible name because of branding, a different entry URL). The intended
flow: record once against a "base" configuration of the vendor product;
replay first with base locators; if a strategy fails for a specific tenant,
look up `tenant_overrides[tenant_id]` for a patch before falling all the way
to hard failure. **Drift detection**: because replay already tries locator
strategies in order and can report which one worked, aggregating that
telemetry across tenant replays over time is exactly how you'd notice "role
matching used to work for tenant X, now only text does" — a leading signal
of upstream markup drift, without needing a human to notice a failure first.
I did not build the override-resolution logic or the drift dashboard; the
schema fields exist and are load-bearing, but the mechanism is designed, not
implemented (see Section 7).

## 5. Escalation & handoff

The control-transfer model (`escalation/models.py`):

1. The browser always runs **non-headless**. This isn't a UI nicety — that
   visible window *is* the live session a human takes over. There's no
   separate "operator copy" to keep in sync.
2. When automation can't proceed, it writes an `InterventionRequest`
   (`status=PENDING`) with the goal, the step it stopped at, why, a
   screenshot, and the current URL — then **blocks**, polling that same
   record. It does not close the page or the browser.
3. A human sees the context (via the mock console, `escalation/console.py`,
   or by just looking at the already-open window) and completes the step
   manually, in that same window, with automation not driving because it's
   blocked.
4. The human hands control back — via the console's button or
   `python -m escalation.resolve <run_id> "notes"` — which records what they
   report having done and flips status to `RESUMED`.
5. Automation wakes up and **re-observes the page** rather than assuming
   what the human did, then continues.

"Who's in control" is answered entirely by `InterventionRequest.status`, and
because automation is synchronously blocked for the whole
`PENDING`/`IN_PROGRESS` window, only one party can act on the page at a
time by construction — no explicit lock was needed.

## 6. Safety

`guardrails/policy.py`, enforced identically for discovery and replay:

- **Allowlist**: domain + route-prefix allowlist, checked before every
  navigation; a separate allowed-action-type set, checked before every step.
  A violation raises `PolicyViolation` and aborts immediately — no partial
  execution past a boundary violation.
- **Risk classification**: every step carries a `RiskLevel`
  (safe/review/irreversible). Only "submit"/"continue"/"open sub-account"-type
  clicks are `IRREVERSIBLE` (they create/mutate a record). Replay **never**
  auto-confirms an irreversible step — `authorize()` raises unless
  `confirmed=True` is explicitly threaded through, which in the current CLI
  only happens for non-irreversible steps; a real deployment would wire this
  to an explicit caller-supplied confirmation flag or an approval gate
  (see Section 7, "confidence & approval" was considered and cut).
- **Redaction**: `redact_dict()` runs on every evidence-log payload before
  it's written, matching field names against a secret/PII pattern list
  (password, token, secret, ssn, etc.) in addition to any field explicitly
  marked `sensitive=True` in a `ParamSpec`.
- **Credentials never touch the artifact or the agent loop at all**:
  `login_session.py` establishes the authenticated session out-of-band and
  saves only a Playwright storage-state (cookies), before discovery or
  replay ever starts. This was a correction I made mid-build: my first
  instinct was to let the LLM log in itself, which would have put a literal
  password into the discovery trace. Treating session establishment as a
  precondition (`CapabilityContract.preconditions`) rather than a recordable
  step is the safer design and mirrors how a production system would call a
  credential vault to mint a session before invoking a capability.

**Limits**: the allowlist is domain/route-based, not selector- or
data-based — it would not catch "the right URL, but reading/writing the
wrong field." Irreversible-action confirmation is currently structural
(hard-coded per action-name in the builder) rather than derived from a
richer taxonomy of what "creates a record" means; a real system would want
this closer to the target app's own semantics.

## 7. Cuts

What's real: the full vertical slice — goal → LLM discovery run → saved
artifact → deterministic replay with input params/outputs/error handling →
human escalation that takes over the live session → evidence for both runs.

What I deliberately cut or mocked, and why:

- **Operator console is bare** (a single Flask page, no auth, no styling) —
  explicitly in-scope to mock per Section 3.6; the real design (blocking
  poll loop + shared live session) is what's real.
- **Expected-outcome authoring is target-app-specific, hand-written in
  `artifact/builder.py`**, not learned or inferred by the LLM during a
  single discovery run. A single happy-path discovery run genuinely
  can't observe every runtime condition it might hit later; a production
  version would seed this from either a library of common patterns per
  vendor product, or a second "exploration" pass that deliberately probes
  known error-prone inputs. I chose to keep the discovery loop simple and
  put this knowledge in the builder instead of building that exploration
  pass.
- **Multi-tenant override resolution and drift telemetry are designed, not
  implemented** (Section 4) — the schema fields exist; the resolution logic
  and dashboard don't.
- **No confidence/approval gating** (stretch goal, skipped): artifacts start
  and stay `DRAFT`; there's no reliability scoring or draft→approved
  workflow gating unattended replay.
- **No stress/stability testing** (replay-N-times signal): the demo runs
  a handful of times manually; no automated flakiness report.

With more time, in priority order: (1) the tenant-override resolver and a
simple drift signal from strategy-used telemetry, since that's the most
direct path from "works for one tenant" to "the real environment" the brief
describes; (2) an approval-gated replay mode, since irreversible-action
safety is the highest-stakes gap right now; (3) a small library of common
expected-outcome patterns per vendor product, to reduce how much of the
error taxonomy has to be hand-authored per artifact.
