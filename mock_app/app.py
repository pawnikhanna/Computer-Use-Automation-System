"""
Mock Legacy Bank Back-Office Application
==========================================
Run with: python app.py  (serves on http://127.0.0.1:5055)
"""
import random
import time
from datetime import datetime, timedelta
from flask import Flask, request, redirect, url_for, session, render_template_string

app = Flask(__name__)
app.secret_key = "dev-only-not-a-real-secret"  # local demo only; never a real secret

# --- Config -----------------------------------------------------------------
SESSION_IDLE_TIMEOUT_SECONDS = 600          # short on purpose, easy to trigger/test
INTERSTITIAL_PROBABILITY = 0.35            # chance the "system notice" appears
SLOW_MEMBER_ID = "55555"                   # this member ID always loads slowly
SLOW_LOAD_SECONDS = 4
LOCKED_MEMBER_ID = "99999"                 # this member ID is permission-denied

# --- "Database" ---------------------------------------------------------------
MEMBERS = {
    "12345": {"name": "Jane Doe", "savings_balance": 4231.50, "checking_balance": 812.02, "status": "active"},
    "20200": {"name": "Marcus Lee", "savings_balance": 150.00, "checking_balance": 0.00, "status": "active"},
    "99999": {"name": "Restricted Record", "savings_balance": 0, "checking_balance": 0, "status": "locked"},
}
NEXT_SUBACCOUNT_NO = 900001


# --- Layout helpers (deliberately old-school: nested tables, no ids) --------
def page(title, body_html, notice_ack_target=None):
    """Wrap body content in the legacy chrome. Injects a dismissible interstitial
    notice some fraction of the time, unless the caller has already acknowledged
    it for this request (notice_ack_target avoids infinite notice loops)."""
    notice_html = ""
    if notice_ack_target and request.args.get("ack") != "1" and random.random() < INTERSTITIAL_PROBABILITY:
        notice_html = f"""
        <table width="100%" bgcolor="#ffefc2" border="1" cellpadding="8">
          <tr><td>
            <b>SYSTEM NOTICE</b><br>
            Displayed balances may be delayed by up to 24 hours due to nightly batch processing.
            <form method="get" action="{notice_ack_target}" style="display:inline">
              <input type="hidden" name="ack" value="1">
              <button type="submit">Acknowledge</button>
            </form>
          </td></tr>
        </table>
        """
    return render_template_string("""
    <html><head><title>{{ title }}</title></head>
    <body style="font-family: 'MS Sans Serif', Tahoma, sans-serif; font-size: 13px;">
    <table width="100%" bgcolor="#003366"><tr><td>
      <font color="white"><b>CORE BANKING ADMIN CONSOLE</b> -- Internal Use Only</font>
    </td></tr></table>
    {{ notice|safe }}
    <table width="100%" cellpadding="10"><tr><td>
    {{ body|safe }}
    </td></tr></table>
    </body></html>
    """, title=title, notice=notice_html, body=body_html)


def require_login():
    if not session.get("user"):
        return False
    last = session.get("last_activity")
    if last and (datetime.utcnow() - datetime.fromisoformat(last)) > timedelta(seconds=SESSION_IDLE_TIMEOUT_SECONDS):
        session.clear()
        return False
    session["last_activity"] = datetime.utcnow().isoformat()
    return True


# --- Routes ------------------------------------------------------------------
@app.route("/", methods=["GET"])
def root():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username and password:
            session["user"] = username
            session["last_activity"] = datetime.utcnow().isoformat()
            return redirect(url_for("dashboard"))
        error = "<p><font color='red'>Username and password are both required.</font></p>"
    body = f"""
    <table>
      <tr><td colspan="2"><b>Operator Login</b></td></tr>
      {error}
      <form method="post">
        <tr><td>Username:</td><td><input type="text" name="username"></td></tr>
        <tr><td>Password:</td><td><input type="password" name="password"></td></tr>
        <tr><td colspan="2"><input type="submit" value="Log In"></td></tr>
      </form>
    </table>
    """
    return page("Login", body)


@app.route("/dashboard")
def dashboard():
    if not require_login():
        return redirect(url_for("login"))
    body = """
    <table>
      <tr><td><b>Welcome, operator.</b></td></tr>
      <tr><td><a href="/members/search">Member Lookup</a></td></tr>
    </table>
    """
    return page("Dashboard", body, notice_ack_target="/dashboard")


@app.route("/members/search", methods=["GET", "POST"])
def member_search():
    if not require_login():
        return redirect(url_for("login"))
    if request.method == "POST":
        mid = request.form.get("member_id", "").strip()
        return redirect(url_for("member_detail", member_id=mid))
    body = """
    <table>
      <tr><td colspan="2"><b>Member Lookup</b></td></tr>
      <form method="post">
        <tr><td>Member ID:</td><td><input type="text" name="member_id"></td></tr>
        <tr><td colspan="2"><input type="submit" value="Search"></td></tr>
      </form>
    </table>
    """
    return page("Member Lookup", body)


@app.route("/members/<member_id>")
def member_detail(member_id):
    if not require_login():
        return redirect(url_for("login"))

    if member_id == SLOW_MEMBER_ID:
        time.sleep(SLOW_LOAD_SECONDS)

    member = MEMBERS.get(member_id)

    if member is None:
        # Legitimate business outcome -- NOT an error.
        body = f"""
        <table>
          <tr><td><b>No member found matching ID "{member_id}".</b></td></tr>
          <tr><td><a href="/members/search">Back to Member Lookup</a></td></tr>
        </table>
        """
        return page("Member Not Found", body)

    if member["status"] == "locked":
        # Permission denial.
        body = f"""
        <table>
          <tr><td><b>Access to member {member_id} is restricted.</b></td></tr>
          <tr><td>Contact a supervisor to request temporary access.</td></tr>
        </table>
        """
        return page("Access Denied", body), 403

    body = f"""
    <table border="1" cellpadding="6">
      <tr><td>Member ID</td><td>{member_id}</td></tr>
      <tr><td>Name</td><td>{member['name']}</td></tr>
      <tr><td>Savings Balance</td><td>${member['savings_balance']:.2f}</td></tr>
      <tr><td>Checking Balance</td><td>${member['checking_balance']:.2f}</td></tr>
    </table>
    <p><a href="/members/{member_id}/open-subaccount">Open Sub-Account for this Member</a></p>
    """
    return page(f"Member {member_id}", body, notice_ack_target=f"/members/{member_id}")


@app.route("/members/<member_id>/open-subaccount", methods=["GET", "POST"])
def open_subaccount(member_id):
    global NEXT_SUBACCOUNT_NO
    if not require_login():
        return redirect(url_for("login"))
    member = MEMBERS.get(member_id)
    if member is None or member["status"] == "locked":
        return redirect(url_for("member_detail", member_id=member_id))

    error = ""
    if request.method == "POST":
        acct_type = request.form.get("account_type", "")
        try:
            deposit = float(request.form.get("initial_deposit", ""))
        except ValueError:
            deposit = -1

        if acct_type not in ("SAVINGS", "MONEY_MARKET") or deposit <= 0:
            error = "<p><font color='red'>Validation error: account type must be SAVINGS or MONEY_MARKET, and initial deposit must be a positive number.</font></p>"
        else:
            # Go to confirmation step (risky/irreversible action needs an explicit
            # confirm step -- see guardrails design).
            return redirect(url_for(
                "confirm_subaccount", member_id=member_id,
                account_type=acct_type, deposit=deposit
            ))

    body = f"""
    <table>
      <tr><td colspan="2"><b>Open Sub-Account -- Member {member_id} ({member['name']})</b></td></tr>
      {error}
      <form method="post">
        <tr><td>Account Type:</td><td>
          <select name="account_type">
            <option value="SAVINGS">Savings</option>
            <option value="MONEY_MARKET">Money Market</option>
          </select>
        </td></tr>
        <tr><td>Initial Deposit:</td><td><input type="text" name="initial_deposit"></td></tr>
        <tr><td colspan="2"><input type="submit" value="Continue"></td></tr>
      </form>
    </table>
    """
    return page("Open Sub-Account", body)


@app.route("/members/<member_id>/open-subaccount/confirm", methods=["GET", "POST"])
def confirm_subaccount(member_id):
    global NEXT_SUBACCOUNT_NO
    if not require_login():
        return redirect(url_for("login"))
    account_type = request.values.get("account_type")
    deposit = request.values.get("deposit")

    if request.method == "POST":
        acct_no = NEXT_SUBACCOUNT_NO
        NEXT_SUBACCOUNT_NO += 1
        body = f"""
        <table border="1" cellpadding="6">
          <tr><td colspan="2"><b>Sub-account created successfully.</b></td></tr>
          <tr><td>New Account Number</td><td>{acct_no}</td></tr>
          <tr><td>Type</td><td>{account_type}</td></tr>
          <tr><td>Initial Deposit</td><td>${float(deposit):.2f}</td></tr>
        </table>
        """
        return page("Sub-Account Confirmation", body)

    body = f"""
    <table>
      <tr><td colspan="2"><b>Please review before confirming (this action is irreversible):</b></td></tr>
      <tr><td>Member ID</td><td>{member_id}</td></tr>
      <tr><td>Account Type</td><td>{account_type}</td></tr>
      <tr><td>Initial Deposit</td><td>${float(deposit):.2f}</td></tr>
      <form method="post">
        <input type="hidden" name="account_type" value="{account_type}">
        <input type="hidden" name="deposit" value="{deposit}">
        <input type="submit" value="Confirm and Open Account">
      </form>
      <form method="get" action="/members/{member_id}/open-subaccount">
        <input type="submit" value="Cancel">
      </form>
    </table>
    """
    return page("Confirm Sub-Account", body)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=False)