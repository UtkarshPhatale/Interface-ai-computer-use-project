# Computer-Use Automation System

A backend integration layer that gives an AI agent "hands" inside a legacy
back-office application with no API: an LLM discovers how to accomplish a
goal by driving the UI, the successful run is recorded as a typed, versioned
**capability artifact**, and that artifact is then **replayed deterministically**
(no LLM) in production, with runtime-error handling, safety guardrails, and
human escalation. See `/REPORT.md` for the full design write-up.

## What's in this repo

| Path | What it is |
|---|---|
| `target_app/` | The proxy target: a deliberately legacy-flavored internal bank servicing app (Flask, table layouts, no test IDs, session auth). Not a real bank system. |
| `artifact/schema.py` | The capability artifact contract (typed, versioned, serializable). |
| `artifact/builder.py` | Turns a raw discovery trace into a reviewable artifact. |
| `agent/discovery.py` | The LLM-driven observe → decide → act loop (Playwright + accessibility tree + Claude). |
| `replay/engine.py` | Deterministic replay engine — the production execution path, no LLM. |
| `guardrails/policy.py` | Allowlist enforcement, risk classification, redaction. |
| `escalation/` | Human-in-the-loop intervention model, mock operator console, CLI hand-back. |
| `evidence/` | Structured run logs + screenshots (auto-generated under `evidence/runs/`) plus the curated demonstration required by the assignment. |
| `run_agent.py` | CLI: run discovery on a goal, save the resulting artifact. |
| `run_replay.py` | CLI: replay a saved artifact deterministically. |
| `login_session.py` | Establishes an authenticated session out-of-band (see REPORT.md — credentials never touch the artifact). |

## Setup (macOS)

```bash
git clone <this-repo-url>
cd interface-ai-project

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
python3 -m playwright install chromium

export ANTHROPIC_API_KEY=sk-ant-...   # your own key
```

## Running it

**1. Start the target app** (in its own terminal, leave it running):

```bash
python3 target_app/server.py
# -> serving on http://127.0.0.1:5055
```

**2. Establish an authenticated session** (credentials for the demo app: `operator` / `demo1234`):

```bash
python3 login_session.py --username operator --password demo1234
```

**3. Run the discovery agent on a goal** (`--headed` shows the real browser window — recommended so you can watch it, and required for the escalation demo below):

```bash
python3 run_agent.py \
  --goal "Look up member 12345 and read their current savings balance" \
  --entry-url http://127.0.0.1:5055/members/search \
  --name lookup_savings_balance \
  --param member_id=12345 \
  --session .session/state.json \
  --headed
```

This prints the saved artifact path, e.g. `artifacts/cap_xxxxxxxx.json`, along
with the exact replay command.

**4. Replay the resulting artifact deterministically** (no LLM):

```bash
python3 run_replay.py \
  --artifact artifacts/cap_xxxxxxxx.json \
  --param member_id=12345 \
  --session .session/state.json \
  --headed
```

**5. See an error/business-outcome case handled** — replay the same artifact
with a member ID that doesn't exist:

```bash
python3 run_replay.py \
  --artifact artifacts/cap_xxxxxxxx.json \
  --param member_id=99999 \
  --session .session/state.json \
  --headed
```

You should see `status: business_outcome`, `code: MEMBER_NOT_FOUND` — not a
crash.

**6. Try the second, deeper goal** (multi-field form + confirmation, an
irreversible action, and a chance of hitting the simulated re-auth
interstitial):

```bash
python3 run_agent.py \
  --goal "Look up member 67890, open a Regular Share sub-account for them with a $50 initial deposit, and reach the confirmation screen" \
  --entry-url http://127.0.0.1:5055/members/search \
  --name open_subaccount \
  --param member_id=67890 \
  --session .session/state.json \
  --headed
```

**7. See the human-escalation handoff.** Force it by running discovery/replay
with a step the agent won't recognize (e.g. interrupt the target app, or add
`--no-escalate` to compare). When escalation triggers, the terminal prints:

```
[ESCALATION] Automation is stuck: <reason>
[ESCALATION] The browser window is still open at <url>.
[ESCALATION] Complete the step manually, then run:
    python -m escalation.resolve <run_id> "what you did"
```

The browser window stays open (same live session) for you to act in. Either
run the printed command, or start the mock operator console in another
terminal and click "Hand control back":

```bash
python3 -m escalation.console
# -> http://127.0.0.1:5099
```

## Running without live services

- `artifact/schema.py`, `artifact/builder.py`, `guardrails/policy.py`, and
  `escalation/models.py` have no external dependencies and can be imported/
  tested without the target app or an LLM.
- `tests/build_sample_artifact.py` builds a artifact by hand (structurally
  identical to what discovery produces) so `replay/engine.py` can be
  exercised against the target app without any LLM calls — this is how the
  replay engine, guardrails, and escalation console were verified during
  development.

## Notes

- This is a demo-scale, single-process system by design (see REPORT.md
  Section 1 and the assignment's "don't reward scaling infrastructure"
  guidance).
- `.session/`, `evidence/runs/`, and `artifacts/` are gitignored (local,
  regenerable). The curated demonstration required by the assignment lives
  in the top-level `evidence/` folders that *are* committed — see REPORT.md
  and the `evidence/` directory listing.
