import json

from artifact.builder import build_artifact
from artifact.schema import CapabilityArtifact
from agent.discovery import DiscoveryTrace, TraceStep


def _fake_trace() -> DiscoveryTrace:
    trace = DiscoveryTrace(
        goal="Look up member and read savings balance",
        entry_url="http://127.0.0.1:5055/members/search",
        params={"member_id": "12345"},
    )
    trace.steps = [
        TraceStep(action_type="navigate", value="http://127.0.0.1:5055/members/search", resulting_url="..."),
        TraceStep(action_type="fill", locator_role="textbox", locator_name="Member ID", value="12345", resulting_url="..."),
        TraceStep(action_type="click", locator_role="button", locator_name="Look Up", resulting_url="..."),
        TraceStep(action_type="click", locator_role="link", locator_name="View Record", resulting_url="..."),
        TraceStep(action_type="assert_state", locator_role="text", locator_name="Member Record", resulting_url="..."),
        TraceStep(action_type="extract", locator_role="text", locator_name="$4,213.55",
                   extract_label="savings_balance", value="$4,213.55", resulting_url="..."),
    ]
    trace.success = True
    trace.done_summary = "Found member and read balance."
    return trace


def test_build_artifact_parameterizes_input_value():
    artifact = build_artifact(_fake_trace(), name="lookup_balance")
    fill_step = next(s for s in artifact.steps if s.value and "{member_id}" in s.value)
    assert fill_step.value == "{member_id}"


def test_build_artifact_round_trips_through_json():
    artifact = build_artifact(_fake_trace(), name="lookup_balance")
    raw = artifact.model_dump_json()
    reloaded = CapabilityArtifact.model_validate_json(raw)
    assert reloaded.artifact_id == artifact.artifact_id
    assert len(reloaded.steps) == len(artifact.steps)


def test_build_artifact_derives_output_schema_from_extracts():
    artifact = build_artifact(_fake_trace(), name="lookup_balance")
    names = {o.name for o in artifact.contract.output_schema}
    assert "savings_balance" in names


def test_build_artifact_rejects_unsuccessful_trace():
    trace = _fake_trace()
    trace.success = False
    try:
        build_artifact(trace, name="x")
        assert False, "should have raised"
    except ValueError:
        pass
