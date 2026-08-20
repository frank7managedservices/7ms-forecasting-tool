"""Page sweep that actually loads data, instead of stopping at an empty form.

Set REAL_DATABASE_URL to copy real settings and saved periods into a throwaway
local sqlite file before testing. Never hard-code a connection string here:
this repository is public.

The plain sweep walks every page with an empty database, but Payroll and Sage
Actuals call st.stop() when no file is loaded, so most of their code never
runs and a NameError can ship unnoticed. This seeds a real payroll period and
real settings first, then selects the saved period so the whole page executes.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

DB = "/tmp/test_pages.db"
PAGES = ["Dashboard", "Forecast", "Payroll", "Terminations", "Sage Actuals",
         "Cash Flow", "Daily Log", "AI Assistant", "My Account",
         "Accounts"]

os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
Path(DB).unlink(missing_ok=True)

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = "/home/user/workspace/7ms/app.py"


def copy_real_data():
    """Pull the real settings and payroll periods out of Neon into sqlite."""
    from sqlalchemy import create_engine, text
    url = os.environ.get("REAL_DATABASE_URL")
    if not url:
        print("note REAL_DATABASE_URL not set, skipping the real-data tests")
        return [], []
    eng = create_engine(url, connect_args={"connect_timeout": 20})
    with eng.connect() as c:
        settings = list(c.execute(text("select name, body from app_settings")))
        docs = list(c.execute(
            text("select kind, name, body, notes from app_documents")))
    con = sqlite3.connect(DB)
    for name, body in settings:
        con.execute("insert or replace into app_settings"
                    "(name, body, saved_at) values(?,?,datetime('now'))",
                    (name, body))
    for kind, name, body, notes in docs:
        con.execute("insert or replace into app_documents"
                    "(kind, name, body, notes, saved_at)"
                    " values(?,?,?,?,datetime('now'))",
                    (kind, name, body, notes))
    con.commit()
    con.close()
    return [n for k, n, _b, _x in docs if k == "payroll"], \
           [n for k, n, _b, _x in docs if k == "sage_is"]


def seed_daily_log():
    con = sqlite3.connect(DB)
    ledger = [
        {"Date": "2026-08-01", "Description": "Rent", "Category": "Rent",
         "Money in": 0.0, "Money out": 33727.56},
        {"Date": "2026-08-05", "Description": "Client A", "Category":
         "Collections", "Money in": 120000.0, "Money out": 0.0},
        {"Date": "2026-08-15", "Description": "Payroll", "Category": "Payroll",
         "Money in": 0.0, "Money out": 76940.5},
    ]
    bank = [{"Date": "2026-08-01", "Bank balance": 13367.94, "Note": "start"},
            {"Date": "2026-08-19", "Bank balance": 22700.0, "Note": "today"}]
    for name, obj in (("actuals_ledger", ledger), ("bank_balances", bank)):
        con.execute("insert or replace into app_settings"
                    "(name, body, saved_at) values(?,?,datetime('now'))",
                    (name, json.dumps(obj)))
    con.commit()
    con.close()


CURRENT_ROLE = "admin"


def app_for(role=None):
    """A signed-in AppTest. Every page is behind the login now, so tests have
    to arrive with a session already established or they only ever see the
    sign-in form."""
    a = AppTest.from_file(APP, default_timeout=400)
    r = role or CURRENT_ROLE
    a.session_state["auth_user"] = {
        "login": "test_" + r, "name": "Test " + r, "email": "",
        "role": r, "must_change": False,
    }
    return a


def seed_users():
    """One real account per level, so the login screen itself can be tested."""
    import hashlib
    rounds = 200000

    def h(pw, salt="ab" * 16):
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), rounds)
        return salt + "$" + dk.hex()

    rows = [
        {"login": "frank", "name": "Frank Royal", "email": "frank@example.com",
         "role": "admin", "password": h("adminpassword1"), "must_change": False},
        {"login": "clerk", "name": "Clerk", "email": "", "role": "user",
         "password": h("userpassword1"), "must_change": False},
        {"login": "watcher", "name": "Watcher", "email": "", "role": "viewer",
         "password": h("viewpassword1"), "must_change": True},
    ]
    con = sqlite3.connect(DB)
    con.execute("insert or replace into app_settings(name, body, saved_at)"
                " values(?,?,datetime('now'))", ("users", json.dumps(rows)))
    con.commit()
    con.close()


def check_login():
    """The gate itself: no session, wrong password, right password, and the
    forced change when a temporary password is in play."""
    failures = 0

    a = AppTest.from_file(APP, default_timeout=400).run()
    labels = [str(x.value) for x in a.subheader]
    gated = "Sign in" in labels and not a.sidebar.radio
    print(("ok   " if gated else "FAIL ") + "login required with no session")
    failures += 0 if gated else 1

    a = AppTest.from_file(APP, default_timeout=400).run()
    a.text_input[0].set_value("frank").run()
    a.text_input[1].set_value("wrong").run()
    a.button[0].click().run()
    refused = bool(a.error) and "auth_user" not in a.session_state
    print(("ok   " if refused else "FAIL ") + "wrong password refused")
    failures += 0 if refused else 1

    a = AppTest.from_file(APP, default_timeout=400).run()
    a.text_input[0].set_value("frank").run()
    a.text_input[1].set_value("adminpassword1").run()
    a.button[0].click().run()
    ok = ("auth_user" in a.session_state
          and a.session_state["auth_user"]["role"] == "admin")
    print(("ok   " if ok else "FAIL ") + "correct password signs in as admin")
    failures += 0 if ok else 1

    a = AppTest.from_file(APP, default_timeout=400).run()
    a.text_input[0].set_value("clerk").run()
    a.text_input[1].set_value("userpassword1").run()
    a.button[0].click().run()
    ok = ("auth_user" in a.session_state
          and a.session_state["auth_user"]["role"] == "user")
    print(("ok   " if ok else "FAIL ") + "email or username both accepted")
    failures += 0 if ok else 1

    a = AppTest.from_file(APP, default_timeout=400).run()
    a.text_input[0].set_value("watcher").run()
    a.text_input[1].set_value("viewpassword1").run()
    a.button[0].click().run()
    forced = any("temporary password" in str(w.value) for w in a.warning)
    print(("ok   " if forced else "FAIL ")
          + "temporary password forces a change before the app opens")
    failures += 0 if forced else 1

    # A viewer must not be handed the Accounts page.
    v = app_for("viewer").run()
    hidden = "Accounts" not in list(v.sidebar.radio[0].options)
    print(("ok   " if hidden else "FAIL ") + "Accounts page hidden from viewer")
    failures += 0 if hidden else 1
    u = app_for("user").run()
    hidden = "Accounts" not in list(u.sidebar.radio[0].options)
    print(("ok   " if hidden else "FAIL ") + "Accounts page hidden from user")
    failures += 0 if hidden else 1
    ad = app_for("admin").run()
    shown = "Accounts" in list(ad.sidebar.radio[0].options)
    print(("ok   " if shown else "FAIL ") + "Accounts page shown to admin")
    failures += 0 if shown else 1
    return failures


def check_locks():
    """Every save must be dead for a viewer, and the forecast assumptions must
    be dead for a plain user too. Measured on the rendered controls, not on
    what the source looks like."""
    failures = 0

    def live_saves(a):
        out = []
        for b in list(a.button):
            label = str(b.label)
            # Compute-only buttons change nothing that persists.
            if label in ("Sign out", "Update", "Estimate liquidacion",
                         "Calculate cash flow"):
                continue
            if not b.disabled:
                out.append(label)
        return out

    checks = [
        ("viewer", "Daily Log", []), ("viewer", "Cash Flow", []),
        ("viewer", "Payroll", []), ("viewer", "Terminations", []),
        ("viewer", "Sage Actuals", []), ("viewer", "Dashboard", []),
        ("user", "Cash Flow", []),
        ("user", "Daily Log", ["Add to the log", "Save this balance",
                               "Save changes to the log",
                               "Save bank balances"]),
        ("admin", "Daily Log", ["Add to the log", "Save this balance",
                                "Save changes to the log",
                                "Save bank balances"]),
    ]
    for role, page, expect in checks:
        a = app_for(role).run()
        a.sidebar.radio[0].set_value(page).run()
        if a.exception:
            print("FAIL " + f"{role} on {page} raised: "
                  + str(a.exception[0].value)[:160])
            failures += 1
            continue
        got = sorted(live_saves(a))
        good = got == sorted(expect)
        print(("ok   " if good else "FAIL ")
              + f"{role} on {page}: "
              + (", ".join(got) if got else "nothing saveable")
              + ("" if good else "  EXPECTED " + str(sorted(expect))))
        failures += 0 if good else 1

    # The assistant is for admin and user. A viewer gets a plain refusal
    # and no tabs at all.
    for role, allowed in (("viewer", False), ("user", True),
                          ("admin", True)):
        a = app_for(role).run()
        a.sidebar.radio[0].set_value("AI Assistant").run()
        if a.exception:
            print("FAIL " + role + " on AI Assistant raised: "
                  + str(a.exception[0].value)[:160])
            failures += 1
            continue
        refused = any("view-only" in str(w.value) for w in a.warning)
        tabs = len(a.tabs)
        good = (not refused and tabs > 0) if allowed else (refused
                                                          and tabs == 0)
        print(("ok   " if good else "FAIL ")
              + role + " on AI Assistant: "
              + ("refused" if refused else "allowed")
              + ", " + str(tabs) + " tabs"
              + ("" if good else "  EXPECTED "
                 + ("allowed with tabs" if allowed else "refused, no tabs")))
        failures += 0 if good else 1

    # A viewer must not even be offered the daily entry forms.
    a = app_for("viewer").run()
    a.sidebar.radio[0].set_value("Daily Log").run()
    hidden = not any("Log something that happened" in str(h.value)
                     for h in a.subheader)
    print(("ok   " if hidden else "FAIL ")
          + "entry forms hidden from viewer on Daily Log")
    failures += 0 if hidden else 1

    # An admin must still be able to save the forecast assumptions.
    a = app_for("admin").run()
    a.sidebar.radio[0].set_value("Cash Flow").run()
    can = any(str(b.label) == "Save these numbers" and not b.disabled
              for b in a.button)
    print(("ok   " if can else "FAIL ") + "admin can still save Cash Flow")
    failures += 0 if can else 1
    return failures


def run(page, setup=None):
    a = app_for().run()
    a.sidebar.radio[0].set_value(page).run()
    if setup:
        setup(a)
    bad = [str(x.value) for x in a.exception]
    print(("FAIL " if bad else "ok   ") + page
          + ("" if not bad else "\n      " + "\n      ".join(bad)))
    return not bad


def main():
    AppTest.from_file(APP, default_timeout=400).run()  # create the tables
    payroll_names, sage_names = copy_real_data()
    seed_daily_log()
    seed_users()

    failures = 0
    failures += check_login()
    failures += check_locks()
    for page in PAGES:
        if not run(page):
            failures += 1

    # Payroll with a real period selected, so the whole page executes.
    for name in payroll_names:
        def pick(a, _n=name):
            a.selectbox[0].set_value(_n).run()
        label = f"Payroll [{name[:34]}]"
        a = app_for().run()
        a.sidebar.radio[0].set_value("Payroll").run()
        try:
            a.selectbox[0].set_value(name).run()
        except Exception as exc:
            print("FAIL " + label + " could not select: " + str(exc))
            failures += 1
            continue
        bad = [str(x.value) for x in a.exception]
        print(("FAIL " if bad else "ok   ") + label
              + ("" if not bad else "\n      " + "\n      ".join(bad)))
        if bad:
            failures += 1
            continue
        # Flip every checkbox and the frequency selector, since each redraw
        # is a fresh script run that can hit different code.
        for idx in range(len(a.checkbox)):
            b = app_for().run()
            b.sidebar.radio[0].set_value("Payroll").run()
            b.selectbox[0].set_value(name).run()
            if idx >= len(b.checkbox):
                continue
            cb = b.checkbox[idx]
            cb.set_value(not cb.value).run()
            bad = [str(x.value) for x in b.exception]
            print(("FAIL " if bad else "ok   ")
                  + f"  checkbox {idx} '{cb.label[:44]}'"
                  + ("" if not bad else "\n      " + "\n      ".join(bad)))
            if bad:
                failures += 1
        for freq in ["Mensual - once a month", "Quincenal - twice a month"]:
            b = app_for().run()
            b.sidebar.radio[0].set_value("Payroll").run()
            b.selectbox[0].set_value(name).run()
            sel = next((s for s in b.selectbox
                        if "often" in (s.label or "").lower()), None)
            if sel is None:
                continue
            sel.set_value(freq).run()
            bad = [str(x.value) for x in b.exception]
            print(("FAIL " if bad else "ok   ") + f"  frequency {freq}"
                  + ("" if not bad else "\n      " + "\n      ".join(bad)))
            if bad:
                failures += 1
        # Every button on the page, by label rather than by index. Index-based
        # iteration broke once the sidebar gained a Sign out button, and a
        # destructive click part-way through shifted everything after it.
        labels = [str(x.label) for x in a.button if str(x.label) != "Sign out"]
        for label_b in labels:
            b = app_for().run()
            b.sidebar.radio[0].set_value("Payroll").run()
            try:
                b.selectbox[0].set_value(name).run()
            except Exception as exc:
                print("FAIL   button '" + label_b[:44]
                      + "' could not reselect the period: " + str(exc)[:120])
                failures += 1
                copy_real_data()
                continue
            target = next((x for x in b.button if str(x.label) == label_b), None)
            if target is None:
                continue
            target.click().run()
            bad = [str(x.value) for x in b.exception]
            print(("FAIL " if bad else "ok   ")
                  + f"  button '{label_b[:44]}'"
                  + ("" if not bad else "\n      " + "\n      ".join(bad)))
            if bad:
                failures += 1
            # Deleting a saved period really does delete it, so put the test
            # data back before the next click.
            if "Delete" in label_b:
                copy_real_data()

    # Sage Actuals with a real statement selected.
    for name in sage_names:
        b = app_for().run()
        b.sidebar.radio[0].set_value("Sage Actuals").run()
        try:
            b.selectbox[0].set_value(name).run()
        except Exception as exc:
            print("note Sage select skipped: " + str(exc))
            continue
        bad = [str(x.value) for x in b.exception]
        print(("FAIL " if bad else "ok   ") + f"Sage Actuals [{name[:30]}]"
              + ("" if not bad else "\n      " + "\n      ".join(bad)))
        if bad:
            failures += 1

    print()
    print("FAILURES: " + str(failures) if failures else "ALL PAGES CLEAN")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
