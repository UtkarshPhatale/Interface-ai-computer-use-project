"""
Cost estimate: discovery (LLM-driven) vs. replay (deterministic, zero LLM).

This is explicitly an ESTIMATE, not a measured bill -- events.jsonl logs
that an LLM call happened (`llm_decision`) but not its actual token counts,
so token usage per call is modeled from typical observed ranges for this
kind of prompt (a DOM/accessibility-tree snapshot + short history + a
small structured tool-call response), not read from real usage logs. If
exact numbers matter later, the real fix is to log input/output token
counts directly in agent/discovery.py at each API call -- noted as a
follow-up in FINDINGS.md, not done here.

Pricing: Claude Sonnet 4.6 (the model this project calls, see
agent/discovery.py's MODEL constant), publicly listed at $3/M input
tokens and $15/M output tokens as of this writing. Source checked at
metric-generation time, not hardcoded from training data.

Call counts ARE real: pulled directly from `llm_decision` event counts in
evidence/runs/discovery-*/events.jsonl for every genuinely-completed
discovery run (aborted/guardrail-blocked/session-expired runs excluded,
same filtering logic as dashboard/report.py's success-rate fix).
"""
from __future__ import annotations

import json
from pathlib import Path

EVIDENCE_ROOT = Path(__file__).parent.parent / "evidence" / "runs"

# Sonnet 4.6 public per-token pricing, USD per million tokens.
INPUT_PRICE_PER_M = 3.00
OUTPUT_PRICE_PER_M = 15.00

# Modeled per-call token estimate for this project's discovery prompt shape
# (accessibility-tree snapshot + short running history + a small structured
# tool-call decision back). NOT measured -- see module docstring. Kept as
# a low/typical/high range rather than a single point estimate, since the
# real value depends on page complexity and how much history has
# accumulated by a given step.
INPUT_TOKENS_PER_CALL_RANGE = (1500, 3500, 6000)   # low, typical, high
OUTPUT_TOKENS_PER_CALL_RANGE = (100, 200, 400)      # low, typical, high


def _completed_discovery_call_counts() -> list[int]:
    """llm_decision counts for discovery runs that actually completed
    (discovery_end with success=True), excluding aborted/blocked/expired
    attempts -- same "don't count non-attempts" discipline as the
    dashboard's success-rate fix."""
    counts = []
    if not EVIDENCE_ROOT.exists():
        return counts
    for run_dir in sorted(EVIDENCE_ROOT.iterdir()):
        if not run_dir.is_dir() or not run_dir.name.startswith("discovery"):
            continue
        events_path = run_dir / "events.jsonl"
        if not events_path.exists():
            continue
        n_decisions = 0
        completed = False
        for line in open(events_path):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") == "llm_decision":
                n_decisions += 1
            if e.get("event") == "discovery_end" and e.get("success") == "True":
                completed = True
        if completed and n_decisions > 0:
            counts.append(n_decisions)
    return counts


def estimate_cost_per_call(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000) * INPUT_PRICE_PER_M + \
           (output_tokens / 1_000_000) * OUTPUT_PRICE_PER_M


def main() -> None:
    counts = _completed_discovery_call_counts()
    if not counts:
        print("No completed discovery runs found.")
        return

    avg_calls = sum(counts) / len(counts)
    print(f"Completed discovery runs analyzed: {len(counts)}")
    print(f"LLM calls per run -- observed: {counts}")
    print(f"Average LLM calls per discovery run: {avg_calls:.1f} "
          f"(min {min(counts)}, max {max(counts)})\n")

    print(f"{'Scenario':<10} {'$/call':>10} {'$/discovery run':>18} {'$/1000 replays':>18}")
    for label, in_tok, out_tok in zip(
        ("low", "typical", "high"),
        INPUT_TOKENS_PER_CALL_RANGE,
        OUTPUT_TOKENS_PER_CALL_RANGE,
    ):
        cost_per_call = estimate_cost_per_call(in_tok, out_tok)
        cost_per_discovery_run = cost_per_call * avg_calls
        # The whole point: replay makes zero LLM calls, so re-running the
        # SAME capability 1000 times via discovery would cost this much;
        # via replay, it costs $0 in LLM spend (server/compute cost aside).
        cost_per_1000_replays_if_rediscovered = cost_per_discovery_run * 1000
        print(f"{label:<10} ${cost_per_call:>9.4f} ${cost_per_discovery_run:>17.4f} "
              f"${cost_per_1000_replays_if_rediscovered:>17,.2f}")

    typical_cost_per_run = estimate_cost_per_call(
        INPUT_TOKENS_PER_CALL_RANGE[1], OUTPUT_TOKENS_PER_CALL_RANGE[1]
    ) * avg_calls
    print(
        f"\nHeadline (typical case): running the same task via discovery "
        f"every time costs an estimated ${typical_cost_per_run:.3f}/run in "
        f"LLM spend. Replaying the saved capability artifact instead costs "
        f"$0 in LLM spend, at any volume -- the entire point of separating "
        f"discovery (one-time, LLM-driven) from replay (repeatable, "
        f"deterministic)."
    )


if __name__ == "__main__":
    main()