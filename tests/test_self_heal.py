"""
Tests for replay/self_heal.py's pure-logic pieces: version bumping and
artifact patching. Deliberately does NOT test SelfHealer.propose_locator()
here -- that requires a live Anthropic API key and is exercised manually
(see README's self-healing section), consistent with how agent/discovery.py's
LLM-calling code isn't unit-tested either. What's tested here is everything
that should be correct regardless of what the LLM proposes: version
semantics and that healing is additive, never destructive.
"""
import json

from artifact.schema import (
    ActionType, CapabilityArtifact, CapabilityContract, Locator,
    LocatorKind, LocatorStrategy, Step,
)
from replay.self_heal import apply_heal_and_save, bump_patch_version


def test_bump_patch_version_increments_patch_only():
    assert bump_patch_version("1.0.0") == "1.0.1"
    assert bump_patch_version("1.0.5") == "1.0.6"
    assert bump_patch_version("2.3.9") == "2.3.10"


def test_bump_patch_version_falls_back_gracefully_on_unexpected_format():
    """Shouldn't crash on a version string that isn't strict semver --
    degrade to a clearly-marked suffix instead."""
    assert bump_patch_version("weird") == "weird-healed"
    assert bump_patch_version("1.0") == "1.0-healed"


def _make_test_artifact() -> CapabilityArtifact:
    return CapabilityArtifact(
        artifact_id="cap_test",
        name="test_cap",
        contract=CapabilityContract(
            goal_description="test", target_app="test", entry_url="http://x",
            input_schema=[], output_schema=[],
        ),
        steps=[
            Step(
                step_id="s01", action=ActionType.CLICK,
                target=Locator(
                    description="button 'Search'",
                    strategies=[LocatorStrategy(kind=LocatorKind.ROLE, value="Search", role="button")],
                ),
            ),
        ],
        success_checkpoint=Locator(
            description="done", strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="Done")],
        ),
    )


def test_apply_heal_and_save_is_additive_not_destructive(tmp_path):
    artifact = _make_test_artifact()
    new_strategy = LocatorStrategy(kind=LocatorKind.XPATH, value="//div[text()='Search']")

    out_path = apply_heal_and_save(artifact, artifact.steps[0], new_strategy, tmp_path)
    healed = json.loads(out_path.read_text())

    kinds = [s["kind"] for s in healed["steps"][0]["target"]["strategies"]]
    assert kinds == ["role", "xpath"], "Original strategy must be kept, new one appended after it"


def test_apply_heal_and_save_bumps_version_and_writes_new_file(tmp_path):
    artifact = _make_test_artifact()
    new_strategy = LocatorStrategy(kind=LocatorKind.TEXT, value="Search")

    out_path = apply_heal_and_save(artifact, artifact.steps[0], new_strategy, tmp_path)

    assert out_path.name == "cap_test_v1.0.1.json"
    healed = json.loads(out_path.read_text())
    assert healed["version"] == "1.0.1"
    assert healed["artifact_id"] == "cap_test"  # same capability identity, new version


def test_apply_heal_and_save_never_mutates_the_original_in_memory(tmp_path):
    artifact = _make_test_artifact()
    original_version = artifact.version
    original_strategy_count = len(artifact.steps[0].target.strategies)

    apply_heal_and_save(
        artifact, artifact.steps[0],
        LocatorStrategy(kind=LocatorKind.CSS, value="#search-btn"),
        tmp_path,
    )

    assert artifact.version == original_version
    assert len(artifact.steps[0].target.strategies) == original_strategy_count