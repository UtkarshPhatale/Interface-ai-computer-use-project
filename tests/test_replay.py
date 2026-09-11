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