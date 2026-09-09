"""
Second proxy target application: a deliberately LOW-ACCESSIBILITY internal
order-tracking tool, used to test whether the agent/replay pipeline
generalizes beyond target_app (which, despite ugly 1998-era table layout,
still uses semantic <a>/<button>/<input> elements Playwright's accessibility
tree can query by role+name).

This app intentionally does the opposite:
- "Buttons" are <div onclick=...> with no role and no button semantics.
- The search input has no <label>, no name/id/aria-label -- just a sibling
  <span> of plain text next to it, exactly the "nearest visible label text"
  fallback case discovery's system prompt already anticipates.
- No table layout at all (a div-soup layout instead), to make sure success
  isn't secretly dependent on <table>/<tr>/<td> structure specifically.

Different domain (package tracking, not banking) on purpose, so this reads
as a genuinely separate target, not a reskin of target_app.
"""
import time
from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "dev-only-not-a-real-secret"  # local demo app only

PACKAGES = {
    "PK-1001": {"id": "PK-1001", "recipient": "Dana Whitfield", "status": "In Transit", "eta": "Sep 12"},
    "PK-1002": {"id": "PK-1002", "recipient": "Marcus Oyelaran", "status": "Delivered", "eta": "Sep 08"},
    "PK-9000": {"id": "PK-9000", "recipient": "Held Package", "status": "On Hold", "eta": "Unknown", "held": True},
}

LOGIN_USER = "dispatcher"
LOGIN_PASS = "track2026"
SESSION_TIMEOUT_SECONDS = 600


def logged_in():
    return session.get("user") == LOGIN_USER and \
        (time.time() - session.get("login_ts", 0)) < SESSION_TIMEOUT_SECONDS


@app.route("/", methods=["GET"])
def home():
    if not logged_in():
        return redirect(url_for("login"))
    return redirect(url_for("package_search"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == LOGIN_USER and p == LOGIN_PASS:
            session["user"] = LOGIN_USER
            session["login_ts"] = time.time()
            return redirect(url_for("package_search"))
        error = "Invalid dispatcher credentials."
    return render_template("v2_login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/packages/search", methods=["GET", "POST"])
def package_search():
    if not logged_in():
        return redirect(url_for("login"))
    result = None
    not_found = False
    query_id = ""
    if request.method == "POST":
        query_id = request.form.get("package_id", "").strip()
        result = PACKAGES.get(query_id)
        not_found = result is None
    return render_template(
        "v2_search.html", result=result, not_found=not_found, query_id=query_id
    )


@app.route("/packages/<package_id>", methods=["GET"])
def package_detail(package_id):
    if not logged_in():
        return redirect(url_for("login"))
    pkg = PACKAGES.get(package_id)
    if pkg is None:
        return render_template("v2_not_found.html", package_id=package_id), 404
    if pkg.get("held"):
        return render_template("v2_held.html", package=pkg), 403
    return render_template("v2_detail.html", package=pkg)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5056, debug=False)