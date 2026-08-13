#!/usr/bin/env python3
"""
Run the LLM-driven discovery agent on a goal, then build and save a
reusable capability artifact from the resulting trace.

Usage:
    python run_agent.py \\
        --goal "Look up member {member_id} and open a regular share sub-account with a $50 deposit, reaching the confirmation screen" \\
        --entry-url http://127.0.0.1:5055/members/search \\
        --name open_subaccount --param member_id=12345 --headed
"""
import argparse
import json
import sys
from pathlib import Path

from agent.discovery import DiscoveryAgent
from artifact.builder import build_artifact
from evidence.logger import RunLogger
from guardrails.policy import AllowlistConfig, GuardrailEngine

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


def parse_params(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"--param must be key=value, got '{p}'")
        k, v = p.split("=", 1)
        out[k] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", required=True)
    ap.add_argument("--entry-url", required=True)
    ap.add_argument("--name", required=True, help="Artifact name, e.g. 'open_subaccount'")
    ap.add_argument("--param", action="append", default=[], help="key=value, repeatable")
    ap.add_argument("--headed", action="store_true", help="Show the browser window (recommended on macOS)")
    ap.add_argument("--session", default=None, help="Path to a storage-state JSON from login_session.py")
    ap.add_argument("--no-escalate", action="store_true", help="Disable human escalation (fail instead of blocking)")
    args = ap.parse_args()

    params = parse_params(args.param)
    logger = RunLogger.create(mode="discovery")
    guardrails = GuardrailEngine(AllowlistConfig())
    agent = DiscoveryAgent(
        guardrails=guardrails, logger=logger, headless=not args.headed,
        escalate_enabled=not args.no_escalate, storage_state_path=args.session,
    )

    print(f"Discovery run: {logger.run_id}")
    print(f"Evidence directory: {logger.dir}")

    trace = agent.run(goal=args.goal, entry_url=args.entry_url, params=params)

    if not trace.success:
        print(f"\nDiscovery FAILED to complete the goal. Reason: {trace.stuck_reason}")
        print(f"See evidence at {logger.dir}")
        sys.exit(1)

    print(f"\nDiscovery SUCCEEDED: {trace.done_summary}")
    artifact = build_artifact(trace, name=args.name)

    ARTIFACTS_DIR.mkdir(exist_ok=True)
    out_path = ARTIFACTS_DIR / f"{artifact.artifact_id}.json"
    out_path.write_text(artifact.model_dump_json(indent=2))
    logger.save_artifact(artifact.model_dump_json(indent=2))

    print(f"\nSaved capability artifact: {out_path}")
    print(f"  artifact_id: {artifact.artifact_id}")
    print(f"  name:        {artifact.name}")
    print(f"  steps:       {len(artifact.steps)}")
    print(f"\nReplay it with:\n  python run_replay.py --artifact {out_path} " +
          " ".join(f'--param {k}={v}' for k, v in params.items()))


if __name__ == "__main__":
    main()
