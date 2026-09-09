#!/usr/bin/env python3
"""
Establishes an authenticated operator session and saves Playwright storage
state (cookies) to .session/state.json, so discovery/replay runs can start
from an already-logged-in state.

Why this is a separate script, not a step inside the agent loop: credentials
must never be persisted into a capability artifact or evidence log (Section
3.4, "never persist secrets or raw sensitive data"). If the discovery agent
were allowed to type a username/password itself, that literal value could
get recorded into the trace. Keeping session establishment as its own
out-of-band step -- the way a real system would call a credential vault to
mint a session before invoking a capability -- means capability artifacts
only ever declare "authenticated operator session" as a precondition
(artifact/schema.py CapabilityContract.preconditions) and never touch
credentials directly.

Usage:
    python login_session.py --username operator --password demo1234
"""
import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

SESSION_DIR = Path(__file__).parent / ".session"
STATE_PATH = SESSION_DIR / "state.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login-url", default="http://127.0.0.1:5055/login")
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    SESSION_DIR.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_page()
        page.goto(args.login_url)

        # Try the "normal" semantic markup path first (target_app / port 5055):
        # input[name=username], input[name=password], role=button "Sign In".
        # Fall back to target_app_v2's deliberately low-semantics markup
        # (id=userfield/passfield, no <button> at all -- a div wired via JS)
        # so this one script can bootstrap a session against either target
        # without needing a separate script per app.
        #
        # Note: check is_visible(), not just count() > 0 -- v2 also has
        # input[name=username]/[name=password], but as *hidden* fields that
        # JS populates on submit, so a presence-only check would silently
        # match the wrong (invisible, unfillable) element.
        username_box = page.locator("input[name=username]")
        if username_box.count() == 0 or not username_box.first.is_visible():
            username_box = page.locator("#userfield")
        username_box.first.fill(args.username)

        password_box = page.locator("input[name=password]")
        if password_box.count() == 0 or not password_box.first.is_visible():
            password_box = page.locator("#passfield")
        password_box.first.fill(args.password)

        sign_in_button = page.get_by_role("button", name="Sign In")
        if sign_in_button.count() > 0:
            sign_in_button.click()
        else:
            # target_app_v2: "Sign In" is a <div onclick=...>, not a real
            # button, so it has no accessible role Playwright can query by
            # role. Fall back to matching on visible text instead.
            page.get_by_text("Sign In", exact=True).click()

        page.wait_for_load_state("networkidle")
        page.context.storage_state(path=str(STATE_PATH))
        browser.close()

    print(f"Session established. Storage state saved to {STATE_PATH}")


if __name__ == "__main__":
    main()