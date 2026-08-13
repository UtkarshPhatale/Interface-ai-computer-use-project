"""
Evidence / observability (Section 3.5).

Every discovery and replay run gets its own timestamped directory under
evidence/runs/<run_id>/ containing:
  - events.jsonl   structured log, one JSON object per line: what happened and why
  - screenshots/   PNG on every step during discovery; PNG on failure during replay
  - artifact.json  (discovery only) the emitted capability artifact

Design choice: JSONL over a DB. This is a demo-scale system: no query needs
justify a database, and JSONL is trivially diffable/reviewable in a PR the
same way the artifact itself is (Section 3.2's "reviewable" requirement
extends naturally to evidence).

All event payloads are passed through guardrails.policy.redact_dict before
being written, so secrets/PII never land on disk even in debug evidence.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from guardrails.policy import redact_dict

EVIDENCE_ROOT = Path(__file__).parent / "runs"


@dataclass
class RunLogger:
    run_id: str
    mode: str  # "discovery" | "replay"
    dir: Path = field(init=False)

    def __post_init__(self):
        self.dir = EVIDENCE_ROOT / self.run_id
        (self.dir / "screenshots").mkdir(parents=True, exist_ok=True)
        self._events_path = self.dir / "events.jsonl"

    @classmethod
    def create(cls, mode: str) -> "RunLogger":
        run_id = f"{mode}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        return cls(run_id=run_id, mode=mode)

    def log(self, event_type: str, sensitive_fields: set[str] | None = None, **payload) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "mode": self.mode,
            "event": event_type,
            **redact_dict(payload, sensitive_fields),
        }
        with open(self._events_path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def screenshot_path(self, label: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        return self.dir / "screenshots" / f"{int(time.time()*1000)}_{safe}.png"

    def save_artifact(self, artifact_json: str) -> Path:
        p = self.dir / "artifact.json"
        p.write_text(artifact_json)
        return p

    def save_result(self, result_json: str) -> Path:
        p = self.dir / "result.json"
        p.write_text(result_json)
        return p
