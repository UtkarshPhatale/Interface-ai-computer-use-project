"""
Tests for replay/engine.py's locator resolution, focused on the None-target
edge case found via dashboard.diagnosis against real evidence data
(evidence/runs/replay-20260813T205949-591450): a `fill` step with
target=None crashed _resolve() with an unhandled AttributeError instead of
a clear, actionable error.
"""
import pytest

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

    class _NoMatchPage:
        def get_by_text(self, *a, **k):
            return type("_Result", (), {"first": _EmptyLocator()})()

    locator = Locator(
        description="text 'Does Not Exist'",
        strategies=[LocatorStrategy(kind=LocatorKind.TEXT, value="Does Not Exist")],
    )
    with pytest.raises(LookupError):
        _resolve(_NoMatchPage(), locator)