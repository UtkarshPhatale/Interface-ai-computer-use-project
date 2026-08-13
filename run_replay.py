#!/usr/bin/env python3
"""
Replay a saved capability artifact deterministically -- no LLM in the loop.
This is the path an AI agent would trigger in production.

Usage:
    python run_replay.py --artifact artifacts/cap_xxxx.json --param member_id=12345 --headed
"""
import argparse
import json
import sys
from pathlib import Path

from artifact.schema import CapabilityArtifact
from evidence.logger import RunLogger
from guardrails.policy import AllowlistConfig, GuardrailEngine
from replay.engine import ReplayEngine


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
    ap.add_argument("--artifact", required=True, help="Path to a saved artifact JSON file")
    ap.add_argument("--param", action="append", default=[], help="key=value, repeatable")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--session", default=None, help="Path to a storage-state JSON from login_session.py")
    ap.add_argument("--no-escalate", action="store_true")
    args = ap.parse_args()

    artifact = CapabilityArtifact.model_validate_json(Path(args.artifact).read_text())
    params = parse_params(args.param)

    logger = RunLogger.create(mode="replay")
    guardrails = GuardrailEngine(AllowlistConfig())
    engine = ReplayEngine(
        guardrails=guardrails, logger=logger, headless=not args.headed,
        escalate_enabled=not args.no_escalate, storage_state_path=args.session,
    )

    print(f"Replay run: {logger.run_id}")
    print(f"Artifact:   {artifact.artifact_id} ({artifact.name}, v{artifact.version})")
    print(f"Evidence:   {logger.dir}\n")

    result = engine.replay(artifact, params)

    print(f"Status:  {result.status.value}")
    if result.result_code:
        print(f"Code:    {result.result_code}")
    if result.message:
        print(f"Message: {result.message}")
    if result.failed_step_id:
        print(f"Failed at step: {result.failed_step_id}")
        print(f"Expected: {result.expected}")
        print(f"Observed: {result.observed}")
    if result.outputs:
        print(f"Outputs: {json.dumps(result.outputs, indent=2)}")

    sys.exit(0 if result.status.value in ("success", "business_outcome", "recoverable_handled") else 1)


if __name__ == "__main__":
    main()
