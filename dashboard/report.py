"""
Static HTML observability report.

Reads all runs via dashboard.aggregator.load_all_runs() and writes a single
self-contained dashboard/report.html -- no server, no extra dependencies,
just open it in a browser. Regenerate any time with:

    python3 -m dashboard.report
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from dashboard.aggregator import load_all_runs

OUTPUT_PATH = Path(__file__).parent / "report.html"


def build_report() -> str:
    runs = load_all_runs()
    replay_runs = [r for r in runs if r.mode == "replay"]
    discovery_runs = [r for r in runs if r.mode == "discovery"]

    status_counts = Counter(r.status for r in replay_runs if r.status)
    total_replays = len(replay_runs)
    success_rate = (status_counts.get("success", 0) / total_replays * 100) if total_replays else 0.0

    total_retries = sum(r.num_retries for r in runs)
    total_escalations = sum(r.num_escalations for r in runs)

    rows = "\n".join(
        f"<tr>"
        f"<td>{r.run_id}</td>"
        f"<td>{r.mode}</td>"
        f"<td class='status-{r.status or 'none'}'>{r.status or '-'}</td>"
        f"<td>{r.num_steps_executed}</td>"
        f"<td>{r.num_retries}</td>"
        f"<td>{r.num_escalations}</td>"
        f"</tr>"
        for r in runs
    )

    status_bars = "\n".join(
        f"<div class='bar-row'><span class='bar-label'>{status}</span>"
        f"<div class='bar' style='width:{count / max(status_counts.values()) * 100:.0f}%'></div>"
        f"<span class='bar-count'>{count}</span></div>"
        for status, count in status_counts.most_common()
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Computer-Use Automation Agent — Run Report</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; max-width: 900px;
         margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
  h1 {{ font-size: 1.5em; }}
  .stat-grid {{ display: flex; gap: 20px; margin: 24px 0; }}
  .stat-card {{ background: #f5f5f7; border-radius: 10px; padding: 16px 20px; flex: 1; }}
  .stat-card .value {{ font-size: 1.8em; font-weight: 600; }}
  .stat-card .label {{ color: #666; font-size: 0.85em; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin: 6px 0; }}
  .bar-label {{ width: 140px; font-size: 0.9em; }}
  .bar {{ background: #4a7fe8; height: 18px; border-radius: 4px; min-width: 2px; }}
  .bar-count {{ font-size: 0.85em; color: #444; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 20px; font-size: 0.85em; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #eee; }}
  th {{ color: #666; font-weight: 600; }}
  .status-success {{ color: #1a7f37; }}
  .status-hard_failure {{ color: #c62828; }}
  .status-business_outcome {{ color: #8a5a00; }}
</style>
</head>
<body>
  <h1>Computer-Use Automation Agent — Run Report</h1>
  <p style="color:#666">Generated from {len(runs)} logged runs ({len(discovery_runs)} discovery, {len(replay_runs)} replay).</p>

  <div class="stat-grid">
    <div class="stat-card"><div class="value">{success_rate:.0f}%</div><div class="label">Replay success rate</div></div>
    <div class="stat-card"><div class="value">{total_retries}</div><div class="label">Total step retries</div></div>
    <div class="stat-card"><div class="value">{total_escalations}</div><div class="label">Human escalations</div></div>
  </div>

  <h3>Replay outcome breakdown</h3>
  {status_bars if status_bars else "<p>No replay runs with a recorded status yet.</p>"}

  <h3>All runs</h3>
  <table>
    <tr><th>Run ID</th><th>Mode</th><th>Status</th><th>Steps</th><th>Retries</th><th>Escalations</th></tr>
    {rows}
  </table>
</body>
</html>
"""


def main() -> None:
    html = build_report()
    OUTPUT_PATH.write_text(html)
    print(f"Report written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()