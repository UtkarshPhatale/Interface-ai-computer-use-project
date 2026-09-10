"""
Tests for agent/discovery.py's _locate() text-matching fallback.

Regression test for a real bug found via target_app_v2 (evidence/runs/
discovery-20260909T235011-3c5c35): a page containing both a "Package
Search" header and a "Search" clickable div caused get_by_text("Search",
exact=False).first to silently resolve to the (inert) header instead of
the button, because "Search" is a substring of "Package Search" and the
header comes first in DOM order. The click executed without raising any
error and had zero effect -- exactly the kind of silent failure that's
hard to catch without a targeted test, since nothing in the stack trace
points at it.
"""
from playwright.sync_api import sync_playwright

from agent.discovery import _locate

AMBIGUOUS_TEXT_HTML = """
<html><body>
  <b>Package Search</b>
  <div id="target" onclick="document.title='clicked'">Search</div>
</body></html>
"""


def test_locate_text_prefers_exact_match_over_substring():
    """The real element (exact text 'Search') must win over a page element
    whose text merely CONTAINS 'Search' as a substring ('Package Search'),
    even though the substring match appears first in DOM order."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(AMBIGUOUS_TEXT_HTML)

        loc = _locate(page, "text", "Search")
        resolved_id = loc.evaluate("e => e.id")

        browser.close()

    assert resolved_id == "target", (
        "Expected _locate to resolve the exact-text match (the real "
        "button), but it resolved a different element -- likely the "
        "'Package Search' header via unsafe substring matching."
    )


def test_locate_text_falls_back_to_substring_when_no_exact_match_exists():
    """When nothing exactly matches, substring matching should still work
    as a fallback -- this isn't a regression test, it's confirming we
    didn't remove the fallback entirely while fixing the ordering."""
    html = "<html><body><div id='only'>Search Now</div></body></html>"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html)

        loc = _locate(page, "text", "Search")
        resolved_id = loc.evaluate("e => e.id")

        browser.close()

    assert resolved_id == "only"