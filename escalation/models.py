"""
Human-in-the-loop escalation & handoff (Section 3.6).

Control-transfer model (see REPORT.md Section 5 for the full write-up):

  1. The browser is always launched non-headless (a real, visible window) --
     NOT because Playwright needs it, but because that visible window *is*
     the shared live session a human takes over. There is no "operator
     console co-browsing an isolated copy" -- it's the literal same browser
     process, same cookies, same DOM state the automation was just driving.
  2. When automation cannot safely proceed, it writes an InterventionRequest
     (status=PENDING) carrying: the goal/capability, the step it stopped at,
     why, and a screenshot -- then BLOCKS, polling the same record for a
     status change. It does not close the browser or the page.
  3. A human reviews the (mocked) operator console, sees the context, and
     manually completes the step *in that same visible window* using their
     own mouse/keyboard -- automation is not driving during this window,
     because it's blocked in the poll loop.
  4. The human calls resolve() (via the console or the CLI script), which
     records what they report having done and flips status to RESUMED.
  5. Automation wakes up, re-observes the page (it never assumes what the
     human did -- it re-reads state), and continues the loop.

This "who's in control" question is answered by InterventionRequest.status:
PENDING/IN_PROGRESS = human has (or should have) control; RESUMED = control
is back with automation. Only one of the two acts on the page at a time by
construction, since automation is blocked for the entire PENDING/IN_PROGRESS
window.

Storage: a small JSON file per run under evidence/runs/<run_id>/intervention.json.
That's enough for a demo; REPORT.md discusses what a production version needs
(a real queue, auth on the console, concurrent-run support).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class InterventionStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RESUMED = "resumed"
    ABANDONED = "abandoned"


@dataclass
class InterventionRequest:
    intervention_id: str
    run_id: str
    goal: str
    step_id: Optional[str]
    reason: str
    screenshot_path: Optional[str]
    current_url: str
    status: InterventionStatus = InterventionStatus.PENDING
    created_at: float = field(default_factory=time.time)
    resolution_notes: Optional[str] = None
    resolved_at: Optional[float] = None

    def to_dict(self):
        d = asdict(self)
        d["status"] = self.status.value
        return d


class InterventionStore:
    """File-backed store scoped to a single run's evidence directory."""

    def __init__(self, path: Path):
        self.path = path

    def create(self, run_id: str, goal: str, step_id: str | None, reason: str,
               screenshot_path: str | None, current_url: str) -> InterventionRequest:
        req = InterventionRequest(
            intervention_id=f"int_{uuid.uuid4().hex[:8]}",
            run_id=run_id, goal=goal, step_id=step_id, reason=reason,
            screenshot_path=screenshot_path, current_url=current_url,
        )
        self._write(req)
        return req

    def get(self) -> Optional[InterventionRequest]:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text())
        data["status"] = InterventionStatus(data["status"])
        return InterventionRequest(**data)

    def resolve(self, notes: str) -> InterventionRequest:
        req = self.get()
        if req is None:
            raise ValueError("No intervention request found to resolve.")
        req.status = InterventionStatus.RESUMED
        req.resolution_notes = notes
        req.resolved_at = time.time()
        self._write(req)
        return req

    def mark_in_progress(self) -> InterventionRequest:
        req = self.get()
        req.status = InterventionStatus.IN_PROGRESS
        self._write(req)
        return req

    def _write(self, req: InterventionRequest) -> None:
        self.path.write_text(json.dumps(req.to_dict(), indent=2))


def wait_for_resume(store: InterventionStore, poll_interval: float = 1.0, timeout: float = 900.0) -> InterventionRequest:
    """Block until a human flips the intervention to RESUMED, or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        req = store.get()
        if req and req.status == InterventionStatus.RESUMED:
            return req
        time.sleep(poll_interval)
    raise TimeoutError("Timed out waiting for human intervention.")
