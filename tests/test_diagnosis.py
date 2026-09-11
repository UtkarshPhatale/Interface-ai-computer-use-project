"""
Tests for dashboard/diagnosis.py.

Uses constructed result.json/artifact fixtures on a temp evidence root
rather than depending on the real evidence/ and artifacts/ directories --
diagnosis.py's own module-level EVIDENCE_ROOT/ARTIFACTS_ROOT constants are
monkeypatched per-test so this doesn't touch real run history and stays
deterministic regardless of what's actually been run on a given machine.
"""
import json

import dashboard.diagnosis as diagnosis_mod


def _write_run(tmp_path, run_id, result: dict, artifact_id: str = None, artifact: dict = None):
    run_dir = tmp_path / "evidence" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(json.dumps(result))

    if artifact_id and artifact:
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / f"{artifact_id}.json").write_text(json.dumps(artifact))


def _patch_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnosis_mod, "EVIDENCE_ROOT", tmp_path / "evidence" / "runs")
    monkeypatch.setattr(diagnosis_mod, "ARTIFACTS_ROOT", tmp_path / "artifacts")


def test_diagnose_returns_none_for_non_hard_failure(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    _write_run(tmp_path, "replay-success-1", {"status": "success"})

    assert diagnosis_mod.diagnose("replay-success-1") is None


def test_diagnose_missing_tier_gap(tmp_path, monkeypatch):
    """Finding #1 shape: role+text tried, css/xpath never recorded at all."""
    _patch_roots(monkeypatch, tmp_path)
    artifact = {
        "steps": [{
            "step_id": "s01",
            "target": {
                "strategies": [
                    {"kind": "role", "value": "View Record", "role": "link"},
                    {"kind": "text", "value": "View Record"},
                ]
            },
        }]
    }
    _write_run(
        tmp_path, "replay-missing-tier",
        {
            "status": "hard_failure",
            "artifact_id": "cap_test1",
            "failed_step_id": "s01",
            "observed": "LookupError: No strategy resolved for locator: link 'View Record'",
        },
        artifact_id="cap_test1", artifact=artifact,
    )

    d = diagnosis_mod.diagnose("replay-missing-tier")
    assert d is not None
    assert set(d.strategies_missing) == {"css", "xpath"}
    assert "under-specified" in d.likely_cause
    # A text strategy WAS present here, so the honest caveat about the
    # substring-match bug should also be surfaced -- static evidence can't
    # rule it out even though this fixture models the "pure missing-tier"
    # story.
    assert "substring-text-matching" in d.likely_cause


def test_diagnose_none_target_crash(tmp_path, monkeypatch):
    """Finding #2 shape: target=None crashed the old _resolve()."""
    _patch_roots(monkeypatch, tmp_path)
    artifact = {"steps": [{"step_id": "s01", "target": None}]}
    _write_run(
        tmp_path, "replay-none-target",
        {
            "status": "hard_failure",
            "artifact_id": "cap_test2",
            "failed_step_id": "s01",
            "observed": "AttributeError: 'NoneType' object has no attribute 'strategies'",
        },
        artifact_id="cap_test2", artifact=artifact,
    )

    d = diagnosis_mod.diagnose("replay-none-target")
    assert d is not None
    assert "target locator was None" in d.likely_cause
    assert "LookupError" in d.recommendation


def test_diagnose_missing_artifact_file(tmp_path, monkeypatch):
    """The artifact file referenced by result.json doesn't exist on disk --
    diagnosis should say so plainly, not crash."""
    _patch_roots(monkeypatch, tmp_path)
    _write_run(
        tmp_path, "replay-orphaned",
        {
            "status": "hard_failure",
            "artifact_id": "cap_does_not_exist",
            "failed_step_id": "s01",
            "observed": "LookupError: No strategy resolved for locator: text 'X'",
        },
    )

    d = diagnosis_mod.diagnose("replay-orphaned")
    assert d is not None
    assert "Could not locate the failing step" in d.likely_cause


def test_diagnose_non_locator_error(tmp_path, monkeypatch):
    """A hard failure whose observed error isn't a locator-resolution
    problem at all should be labeled as such, not force-fit into one of
    the locator-specific branches."""
    _patch_roots(monkeypatch, tmp_path)
    artifact = {
        "steps": [{
            "step_id": "s01",
            "target": {"strategies": [{"kind": "role", "value": "X", "role": "button"}]},
        }]
    }
    _write_run(
        tmp_path, "replay-other-error",
        {
            "status": "hard_failure",
            "artifact_id": "cap_test3",
            "failed_step_id": "s01",
            "observed": "TimeoutError: Navigation timeout of 30000ms exceeded",
        },
        artifact_id="cap_test3", artifact=artifact,
    )

    d = diagnosis_mod.diagnose("replay-other-error")
    assert d is not None
    assert "non-locator error" in d.likely_cause
    assert "Inspect manually" in d.recommendation