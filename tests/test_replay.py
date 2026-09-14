"""
Tests for replay/engine.py's locator resolution, focused on the None-target
edge case found via dashboard.diagnosis against real evidence data
(evidence/runs/replay-20260813T205949-591450): a `fill` step with
target=None crashed _resolve() with an unhandled AttributeError instead of
a clear, actionable error.
"""
import pytest
from playwright.sync_api import sync_playwright

from artifact.schema import Locator, LocatorKind, LocatorStrategy
from replay.engine import _resolve


class _FakePage:
    """Minimal stand-in; _resolve should raise before touching the page
    at all when locator is None, so no Playwright calls should occur."""
    def get_by_role(self, *a, **k):
        raise AssertionError("Should not be called when locator is None")

    def get_by_text(self, *a, **k):
        raise AssertionError("Should not be called when locator is None")

    def locator(self, *a, **k):
        raise AssertionError("Should not be called when locator is None")


def test_resolve_raises_lookup_error_on_none_target():
    """Regression test: previously raised AttributeError: 'NoneType' object
    has no attribute 'strategies'. Should now raise a clear LookupError."""
    with pytest.raises(LookupError, match="no target locator recorded"):
        _resolve(_FakePage(), None)


def test_resolve_raises_lookup_error_when_no_strategy_matches():
    """Sanity check the pre-existing behavior is unchanged: a locator with
    strategies that don't resolve to any element still raises LookupError,
    not some other exception type."""
    class _EmptyLocator:
        def count(self):
            return 0
        @property
        def first(self):
            return self

    class _NoMatchPage:
        def get_by_text(self, *a, **k):
            return _EmptyLocator()

    locator = Locator(
        description="text 'Does Not Exist'",
        strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="Does Not Exist")],
    )
    with pytest.raises(LookupError):
        _resolve(_NoMatchPage(), locator)


AMBIGUOUS_TEXT_HTML = """
<html><body>
  <b>Package Search</b>
  <div id="target" onclick="document.title='clicked'">Search</div>
</body></html>
"""


def test_resolve_text_strategy_prefers_exact_match_over_substring():
    """Regression test for the SAME bug as
    tests/test_discovery_locate.py::test_locate_text_prefers_exact_match_over_substring,
    but in replay/engine.py's _resolve() instead of agent/discovery.py's
    _locate() -- these are two separate functions in two separate files,
    and fixing one did not fix the other. Root cause of all three
    target_app_v2 replay hard-failures on 2026-09-10 (cap_53043bc026,
    cap_a65b404837, cap_a9447b47e3): _resolve's TEXT strategy used
    exact=False only, so get_by_text("Search").first matched "Package
    Search" (the inert header) before the real "Search" button, the click
    had no effect, and the page never advanced past the search form --
    causing every subsequent step in the artifact to fail too."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(AMBIGUOUS_TEXT_HTML)

        locator = Locator(
            description="text 'Search'",
            strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="Search")],
        )
        loc, strat = _resolve(page, locator)
        resolved_id = loc.evaluate("e => e.id")

        browser.close()

    assert resolved_id == "target", (
        "Expected _resolve to find the exact-text match (the real "
        "button), not the substring-matching header."
    )


def test_run_step_self_heal_success_with_no_expected_outcome_is_not_reported_as_failure(tmp_path, monkeypatch):
    """Regression test for a real bug found in manual end-to-end testing of
    replay/self_heal.py: _attempt_self_heal returning None to mean "healing
    didn't help" was indistinguishable from a successful heal whose step had
    no matching expected_outcome (which also produces None from
    _check_expected_outcomes). The caller (_run_step) treated any None from
    _attempt_self_heal as "fall through to hard failure" -- so a genuinely
    successful heal was being reported as hard_failure. Fixed with a
    dedicated sentinel (_SELF_HEAL_SUCCEEDED_NO_OUTCOME) distinct from None.
    This test exercises _run_step directly (not a live browser) by
    monkeypatching the execution/outcome-checking internals, since the bug
    was in _run_step's control flow, not in any browser interaction."""
    from unittest.mock import MagicMock, patch
    from artifact.schema import (
        ActionType, CapabilityArtifact, CapabilityContract, Locator,
        LocatorKind, LocatorStrategy, Step,
    )
    from evidence.logger import RunLogger
    from guardrails.policy import AllowlistConfig, GuardrailEngine
    from replay.engine import ReplayEngine
    from replay.self_heal import HealResult
 
    artifact = CapabilityArtifact(
        artifact_id="cap_unit", name="unit_test",
        contract=CapabilityContract(goal_description="t", target_app="t", entry_url="http://x",
                                     input_schema=[], output_schema=[]),
        steps=[Step(step_id="s01", action=ActionType.CLICK,
                    target=Locator(description="button 'X'",
                                    strategies=[LocatorStrategy(kind=LocatorKind.ROLE, value="X", role="button")]))],
        success_checkpoint=Locator(description="done", strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="Done")]),
    )
 
    engine = ReplayEngine(
        guardrails=GuardrailEngine(AllowlistConfig()),
        logger=RunLogger.create(mode="replay"),
        self_heal_enabled=True, artifacts_dir=tmp_path,
    )
 
    mock_healer = MagicMock()
    mock_healer.propose_locator.return_value = HealResult(
        healed=True, new_strategy=LocatorStrategy(kind=LocatorKind.TEXT, value="X"), reasoning="t",
    )
 
    # Every normal execution attempt fails (forces the retry loop to exhaust
    # and reach the self-heal branch); the healed-locator execution and the
    # outcome check both succeed with "nothing special happened".
    monkeypatch.setattr(engine, "_execute_step",
                         lambda page, s, params, outputs: (_ for _ in ()).throw(LookupError("no match")))
    monkeypatch.setattr(engine, "_execute_step_with_locator", lambda page, s, params, outputs, loc: None)
    monkeypatch.setattr(engine, "_check_expected_outcomes", lambda page, art, s, params, exc: None)
 
    with patch("replay.self_heal.SelfHealer", return_value=mock_healer), \
         patch("replay.engine._resolve", return_value=(MagicMock(), None)), \
         patch("replay.engine.time.sleep"):
        result = engine._run_step(MagicMock(), artifact, artifact.steps[0], {}, {})
 
    assert result is None, (
        "A successful self-heal with no matching expected_outcome should "
        "behave like an ordinary successful step (None, meaning 'continue "
        "to the next step'), not be reported as hard_failure."
    )
 