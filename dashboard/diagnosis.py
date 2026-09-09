"""
Failure diagnosis (Day 3).

Pure analysis, no LLM call yet -- that's Week 2 (self-healing re-discovery).
This module answers one question: "given a hard_failure run, why did every
configured locator strategy fail, and what's missing?"

Motivating finding (from real evidence, 2026-08-13 runs): both hard-failure
replays failed on a `click` step whose artifact only ever recorded `role` and
`text` strategies -- never `xpath` or `css`, even though replay/engine.py's
_resolve() fully supports all four kinds. Tracing into agent/discovery.py
confirms discovery simply never emits an xpath/css strategy today. So the
real root cause of these two failures isn't "the DOM changed" (classic
self-healing scenario) -- it's "discovery under-populated the strategy list,
so replay had nothing left to fall back to." Diagnosis needs to say that
plainly, not just report a stack trace.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ARTIFACTS_ROOT = Path(__file__).parent.parent / "artifacts"
EVIDENCE_ROOT = Path(__file__).parent.parent / "evidence" / "runs"

KNOWN_STRATEGY_KINDS = {"role", "text", "css", "xpath"}


@dataclass
class FailureDiagnosis:
    run_id: str
    failed_step_id: Optional[str]
    strategies_tried: list[str]
    strategies_missing: list[str]
    likely_cause: str
    recommendation: str


def _load_result(run_id: str) -> Optional[dict]:
    path = EVIDENCE_ROOT / run_id / "result.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _load_step_from_artifact(artifact_id: str, step_id: str) -> Optional[dict]:
    path = ARTIFACTS_ROOT / f"{artifact_id}.json"
    if not path.exists():
        return None
    artifact = json.loads(path.read_text())
    for step in artifact.get("steps", []):
        if step.get("step_id") == step_id:
            return step
    return None


def diagnose(run_id: str) -> Optional[FailureDiagnosis]:
    result = _load_result(run_id)
    if not result or result.get("status") != "hard_failure":
        return None

    failed_step_id = result.get("failed_step_id")
    artifact_id = result.get("artifact_id")
    step = _load_step_from_artifact(artifact_id, failed_step_id) if artifact_id and failed_step_id else None

    if not step:
        return FailureDiagnosis(
            run_id=run_id,
            failed_step_id=failed_step_id,
            strategies_tried=[],
            strategies_missing=[],
            likely_cause="Could not locate the failing step in its artifact for deeper analysis.",
            recommendation="Verify the artifact file still exists and matches the run's artifact_id.",
        )

    target = step.get("target") or {}
    strategies = target.get("strategies", [])
    tried_kinds = [s.get("kind") for s in strategies]
    missing_kinds = sorted(KNOWN_STRATEGY_KINDS - set(tried_kinds))

    observed = result.get("observed", "")
    if "No strategy resolved" in observed and missing_kinds:
        cause = (
            f"All {len(tried_kinds)} configured strategies ({', '.join(tried_kinds)}) failed to "
            f"resolve an element, and the artifact never recorded a fallback for: "
            f"{', '.join(missing_kinds)}. This is a discovery-time gap, not necessarily DOM drift -- "
            f"the capability artifact was under-specified for this step."
        )
        recommendation = (
            "Re-run discovery for this capability with a stronger prompt to capture xpath/css "
            "fallbacks, or add a self-healing re-discovery pass (Week 2) that proposes a locator "
            "for the missing tiers automatically when this pattern is detected."
        )
    elif "No strategy resolved" in observed:
        cause = (
            f"All {len(tried_kinds)} configured strategies ({', '.join(tried_kinds)}) failed, "
            f"including every known strategy kind. The target element likely no longer exists "
            f"on the page at all (real DOM change), not a missing-strategy gap."
        )
        recommendation = "This is a genuine self-healing candidate: capture a DOM snapshot and re-discover this step's locator."
    else:
        cause = f"Step failed with a non-locator error: {observed}"
        recommendation = "Inspect manually; not a locator-resolution failure."

    return FailureDiagnosis(
        run_id=run_id,
        failed_step_id=failed_step_id,
        strategies_tried=tried_kinds,
        strategies_missing=missing_kinds,
        likely_cause=cause,
        recommendation=recommendation,
    )


def diagnose_all_hard_failures() -> list[FailureDiagnosis]:
    diagnoses = []
    if not EVIDENCE_ROOT.exists():
        return diagnoses
    for run_dir in sorted(EVIDENCE_ROOT.iterdir()):
        if not run_dir.is_dir():
            continue
        d = diagnose(run_dir.name)
        if d:
            diagnoses.append(d)
    return diagnoses


if __name__ == "__main__":
    diagnoses = diagnose_all_hard_failures()
    print(f"Diagnosed {len(diagnoses)} hard-failure run(s)\n")
    for d in diagnoses:
        print(f"Run: {d.run_id}")
        print(f"  Failed step: {d.failed_step_id}")
        print(f"  Strategies tried: {d.strategies_tried}")
        print(f"  Strategies missing: {d.strategies_missing}")
        print(f"  Likely cause: {d.likely_cause}")
        print(f"  Recommendation: {d.recommendation}")
        print()