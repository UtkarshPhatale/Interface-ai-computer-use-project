"""
Evidence aggregator (dashboard, part 1).

Walks evidence/runs/* and turns the existing per-run events.jsonl + result.json
(written by evidence.logger.RunLogger) into a flat list of RunSummary records
that the dashboard can chart. This file adds NO new logging -- it only reads
what the agent/replay engine already writes, which is the honest framing for
this project: the observability was designed in from day one (evidence/logger.py),
what was missing was an aggregated view across runs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

EVIDENCE_ROOT = Path(__file__).parent.parent / "evidence" / "runs"


@dataclass
class RunSummary:
    run_id: str
    mode: str  # "discovery" | "replay"
    status: Optional[str] = None          # ReplayStatus value, replay runs only
    result_code: Optional[str] = None
    failed_step_id: Optional[str] = None
    num_steps_executed: int = 0
    num_retries: int = 0
    num_recoveries: int = 0
    num_escalations: int = 0
    had_intervention: bool = False
    locator_kinds_used: list[str] = field(default_factory=list)
    artifact_id: Optional[str] = None
    started_at: Optional[str] = None


def _load_events(run_dir: Path) -> list[dict]:
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        return []
    events = []
    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _load_result(run_dir: Path) -> Optional[dict]:
    result_path = run_dir / "result.json"
    if not result_path.exists():
        return None
    try:
        return json.loads(result_path.read_text())
    except json.JSONDecodeError:
        return None


def summarize_run(run_dir: Path) -> RunSummary:
    events = _load_events(run_dir)
    result = _load_result(run_dir)

    mode = "discovery" if run_dir.name.startswith("discovery") else "replay"
    summary = RunSummary(run_id=run_dir.name, mode=mode)

    if events:
        summary.started_at = events[0].get("ts")
        first_start = next((e for e in events if e.get("event") in ("replay_start", "discovery_start")), None)
        if first_start:
            summary.artifact_id = first_start.get("artifact_id")

    for e in events:
        etype = e.get("event")
        if etype == "step_executed":
            summary.num_steps_executed += 1
        elif etype == "step_retry":
            summary.num_retries += 1
        elif etype == "recoverable_dismissed":
            summary.num_recoveries += 1
        elif etype == "intervention_created":
            summary.num_escalations += 1
            summary.had_intervention = True

    if result:
        summary.status = result.get("status")
        summary.result_code = result.get("result_code")
        summary.failed_step_id = result.get("failed_step_id")
        summary.artifact_id = summary.artifact_id or result.get("artifact_id")

    return summary


def load_all_runs() -> list[RunSummary]:
    if not EVIDENCE_ROOT.exists():
        return []
    runs = []
    for run_dir in sorted(EVIDENCE_ROOT.iterdir()):
        if run_dir.is_dir():
            runs.append(summarize_run(run_dir))
    return runs


if __name__ == "__main__":
    # Quick manual check: run `python -m dashboard.aggregator` from the repo root.
    runs = load_all_runs()
    print(f"Loaded {len(runs)} runs\n")
    for r in runs:
        print(f"{r.run_id:45s} mode={r.mode:9s} status={r.status} "
              f"steps={r.num_steps_executed} retries={r.num_retries} "
              f"escalations={r.num_escalations}")