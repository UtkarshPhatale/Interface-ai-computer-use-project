# Evidence

## What's here already (generated during development, real runs against the live target app)

- `example_artifact.json` — a `CapabilityArtifact`, structurally identical to
  what `artifact/builder.py` produces from a real discovery trace (built by
  `tests/build_sample_artifact.py`, used to develop and test the replay
  engine without needing a live LLM call for every iteration).
- `example_replay_success/` — a real deterministic replay of that artifact
  (`member_id=12345`) against the live target app: `events.jsonl` (structured
  log), `result.json` (`status: success`, extracted `savings_balance`).
- `example_replay_business_outcome_not_found/` — same artifact,
  `member_id=99999` (doesn't exist). `result.json` shows
  `status: business_outcome`, `code: MEMBER_NOT_FOUND` — not a crash — plus a
  screenshot of the not-found banner at the moment it was detected.
- `example_replay_business_outcome_restricted/` — same artifact,
  `member_id=40404` (flagged restricted). `status: business_outcome`,
  `code: MEMBER_RESTRICTED`, with a screenshot.

These three replay runs demonstrate the replay engine, the locator strategy
resolution, and the business-outcome/hard-failure distinction end to end —
but they are **not** the LLM discovery run the assignment requires as "the
heart of the project." That run needs a live Anthropic API key and has to
happen on the machine actually submitting this repo.

## What you (the person running this) still need to add

1. Run the setup in `/README.md`.
2. Run a real discovery goal, e.g.:
   ```bash
   python3 run_agent.py \
     --goal "Look up member 12345 and read their current savings balance" \
     --entry-url http://127.0.0.1:5055/members/search \
     --name lookup_savings_balance --param member_id=12345 \
     --session .session/state.json --headed
   ```
   This creates `evidence/runs/discovery-<timestamp>-<hash>/` containing
   `events.jsonl` (every LLM decision + step executed), `screenshots/` (one
   per step), and `artifact.json` (the emitted artifact).
3. Copy that run into a committed, clearly-labeled folder, e.g.:
   ```bash
   cp -r evidence/runs/discovery-<timestamp>-<hash> evidence/example_discovery_run
   git add evidence/example_discovery_run
   ```
4. Replay the artifact it produced (`run_replay.py`, see `/README.md`) and
   optionally add that run too, the same way.
5. Ideally, also capture one replay that hits an error/exceptional state
   with the *discovery-produced* artifact (not just the hand-built sample
   above), the same way `example_replay_business_outcome_not_found/` does.
