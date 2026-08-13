"""
Minimal operator console (Section 3.6 scope note: "Mock the operator UI if
needed, but make the handoff mechanism and the control-transfer model real").

This is intentionally bare -- a real-time co-browsing UI is explicitly out of
scope (Section 3.6). What's real is InterventionStore + wait_for_resume in
escalation/models.py: THIS console is just a thin, replaceable front-end onto
that same store. Scans evidence/runs/*/intervention.json for pending items.

Run: python -m escalation.console   (serves on http://127.0.0.1:5099)
"""
from __future__ import annotations

import base64
from pathlib import Path

from flask import Flask, redirect, render_template_string, request

from escalation.models import InterventionStatus, InterventionStore

EVIDENCE_RUNS = Path(__file__).parent.parent / "evidence" / "runs"

app = Flask(__name__)

TEMPLATE = """
<html><head><title>Operator Console (mock)</title></head>
<body style="font-family: sans-serif; max-width: 800px; margin: 40px auto;">
<h2>Pending interventions</h2>
{% if not items %}<p>None right now.</p>{% endif %}
{% for run_id, req in items %}
<div style="border:1px solid #ccc; padding:16px; margin-bottom:16px;">
  <p><b>Run:</b> {{ run_id }}<br>
  <b>Goal:</b> {{ req.goal }}<br>
  <b>Stopped at step:</b> {{ req.step_id }}<br>
  <b>Reason:</b> {{ req.reason }}<br>
  <b>Current URL:</b> {{ req.current_url }}<br>
  <b>Status:</b> {{ req.status.value }}</p>
  {% if req.screenshot_b64 %}<img src="data:image/png;base64,{{ req.screenshot_b64 }}" width="500"><br>{% endif %}
  {% if req.status.value != 'resumed' %}
  <form method="POST" action="/resolve/{{ run_id }}">
    <label>Notes on what you did manually:</label><br>
    <textarea name="notes" rows="3" cols="60"></textarea><br>
    <button type="submit">Hand control back to automation</button>
  </form>
  {% endif %}
</div>
{% endfor %}
</body></html>
"""


def _all_pending():
    items = []
    if not EVIDENCE_RUNS.exists():
        return items
    for run_dir in sorted(EVIDENCE_RUNS.iterdir()):
        store = InterventionStore(run_dir / "intervention.json")
        req = store.get()
        if req is None:
            continue
        b64 = None
        if req.screenshot_path and Path(req.screenshot_path).exists():
            b64 = base64.b64encode(Path(req.screenshot_path).read_bytes()).decode()
        req.screenshot_b64 = b64  # type: ignore[attr-defined]
        items.append((run_dir.name, req))
    return items


@app.route("/")
def index():
    return render_template_string(TEMPLATE, items=_all_pending())


@app.route("/resolve/<run_id>", methods=["POST"])
def resolve(run_id: str):
    store = InterventionStore(EVIDENCE_RUNS / run_id / "intervention.json")
    store.resolve(notes=request.form.get("notes", ""))
    return redirect("/")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5099, debug=False)
