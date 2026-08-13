"""
Proxy target application: a deliberately 'legacy' internal bank servicing tool.

Design choices that mimic the real environment (see REPORT.md):
- Server-rendered HTML, no JS framework, no test IDs (no data-testid attributes).
- Table-based layout for the member list (common in old enterprise apps).
- Session-based auth with a short timeout, to exercise "session expiry" as a
  runtime error class the replay engine must detect.
- A permission-denied case (member 40404 is flagged "restricted" -> 403-style page).
- A validation error case (opening a sub-account with a negative/zero deposit).
- An unexpected interstitial dialog (a "confirm re-auth" prompt that appears
  ~1 in N times when opening a sub-account, simulating a flaky real-world modal).

This is NOT a real bank system. It's a local stand-in used only to exercise the
computer-use agent and replay engine, per the assignment's Section 4 guidance.
"""
import random
import time
from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "dev-only-not-a-real-secret"  # local demo app only

# --- In-memory "core banking" data --------------------------------------
MEMBERS = {
    "12345": {"id": "12345", "name": "Jordan Alvarez", "savings_balance": 4213.55, "restricted": False},
    "67890": {"id": "67890", "name": "Priya Natarajan", "savings_balance": 812.10, "restricted": False},
    "40404": {"id": "40404", "name": "Restricted Member", "savings_balance": 0.0, "restricted": True},
}
SUB_ACCOUNTS = []  # created during demo runs

LOGIN_USER = "operator"
LOGIN_PASS = "demo1234"
SESSION_TIMEOUT_SECONDS = 600  # generous for a demo run


def logged_in():
    return session.get("user") == LOGIN_USER and \
        (time.time() - session.get("login_ts", 0)) < SESSION_TIMEOUT_SECONDS


@app.route("/", methods=["GET"])
def home():
    if not logged_in():
        return redirect(url_for("login"))
    return redirect(url_for("member_search"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == LOGIN_USER and p == LOGIN_PASS:
            session["user"] = LOGIN_USER
            session["login_ts"] = time.time()
            return redirect(url_for("member_search"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/members/search", methods=["GET", "POST"])
def member_search():
    if not logged_in():
        return redirect(url_for("login"))
    result = None
    not_found = False
    query_id = ""
    if request.method == "POST":
        query_id = request.form.get("member_id", "").strip()
        result = MEMBERS.get(query_id)
        not_found = result is None
    return render_template(
        "search.html", result=result, not_found=not_found, query_id=query_id
    )


@app.route("/members/<member_id>", methods=["GET"])
def member_detail(member_id):
    if not logged_in():
        return redirect(url_for("login"))
    member = MEMBERS.get(member_id)
    if member is None:
        return render_template("not_found.html", member_id=member_id), 404
    if member["restricted"]:
        return render_template("restricted.html", member=member), 403
    return render_template("detail.html", member=member)


@app.route("/members/<member_id>/open-subaccount", methods=["GET", "POST"])
def open_subaccount(member_id):
    if not logged_in():
        return redirect(url_for("login"))
    member = MEMBERS.get(member_id)
    if member is None:
        return render_template("not_found.html", member_id=member_id), 404
    if member["restricted"]:
        return render_template("restricted.html", member=member), 403

    error = None
    if request.method == "POST":
        acct_type = request.form.get("acct_type", "")
        deposit_raw = request.form.get("initial_deposit", "")
        try:
            deposit = float(deposit_raw)
        except ValueError:
            deposit = None

        if deposit is None or deposit <= 0:
            error = "Initial deposit must be a positive number."
        elif acct_type not in ("regular_share", "holiday_club"):
            error = "Select a valid sub-account type."
        else:
            # Simulate an occasional unexpected re-auth interstitial (~1 in 4),
            # a realistic 'exceptional state' the replay engine must handle.
            if random.random() < 0.25 and not request.form.get("reauth_confirmed"):
                return render_template(
                    "reauth_interstitial.html", member=member,
                    acct_type=acct_type, initial_deposit=deposit_raw
                )
            new_acct_id = f"SUB-{len(SUB_ACCOUNTS) + 1:05d}"
            SUB_ACCOUNTS.append({
                "id": new_acct_id, "member_id": member_id,
                "type": acct_type, "deposit": deposit,
            })
            return render_template(
                "confirmation.html", member=member, acct_id=new_acct_id,
                acct_type=acct_type, deposit=deposit
            )

    return render_template("open_subaccount.html", member=member, error=error)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=False)
