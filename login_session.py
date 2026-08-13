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
        page.get_by_role("textbox", name="Username").or_(page.locator("input[name=username]")).first.fill(args.username)
        page.locator("input[name=password]").fill(args.password)
        page.get_by_role("button", name="Sign In").click()
        page.wait_for_load_state("networkidle")
        page.context.storage_state(path=str(STATE_PATH))
        browser.close()

    print(f"Session established. Storage state saved to {STATE_PATH}")


if __name__ == "__main__":
    main()
