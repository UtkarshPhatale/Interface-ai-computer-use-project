"""
CLI hand-back: `python -m escalation.resolve <run_id> "notes on what you did"`

Flips the run's intervention record to RESUMED, which wakes up the blocked
discovery/replay loop (see escalation.models.wait_for_resume). This is the
CLI-driven twin of the "Hand control back" button in escalation/console.py --
same store, same effect, different front door.
"""
import sys
from pathlib import Path

from escalation.models import InterventionStore

EVIDENCE_RUNS = Path(__file__).parent.parent / "evidence" / "runs"


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m escalation.resolve <run_id> [\"notes\"]")
        sys.exit(1)
    run_id = sys.argv[1]
    notes = sys.argv[2] if len(sys.argv) > 2 else ""
    store = InterventionStore(EVIDENCE_RUNS / run_id / "intervention.json")
    req = store.resolve(notes)
    print(f"Resumed intervention {req.intervention_id} for run {run_id}.")


if __name__ == "__main__":
    main()
