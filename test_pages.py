"""Page sweep that actually loads data, instead of stopping at an empty form.

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
         "Cash Flow", "Daily Log", "AI Assistant"]

os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
Path(DB).unlink(missing_ok=True)

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = "/home/user/workspace/7ms/app.py"


def copy_real_data():
    """Pull the real settings and payroll periods out of Neon into sqlite."""
    from sqlalchemy import create_engine, text
    url = ("postgresql://neondb_owner:npg_BjZLx1irqyd4@"
           "ep-wandering-smoke-acyeoaof-pooler.sa-east-1.aws.neon.tech/"
           "neondb?sslmode=require")
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


def run(page, setup=None):
    a = AppTest.from_file(APP, default_timeout=400).run()
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

    failures = 0
    for page in PAGES:
        if not run(page):
            failures += 1

    # Payroll with a real period selected, so the whole page executes.
    for name in payroll_names:
        def pick(a, _n=name):
            a.selectbox[0].set_value(_n).run()
        label = f"Payroll [{name[:34]}]"
        a = AppTest.from_file(APP, default_timeout=400).run()
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
            b = AppTest.from_file(APP, default_timeout=400).run()
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
            b = AppTest.from_file(APP, default_timeout=400).run()
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
        # Every button on the page, including the send-to-cash-flow buttons.
        for idx in range(len(a.button)):
            b = AppTest.from_file(APP, default_timeout=400).run()
            b.sidebar.radio[0].set_value("Payroll").run()
            b.selectbox[0].set_value(name).run()
            if idx >= len(b.button):
                continue
            label_b = b.button[idx].label
            b.button[idx].click().run()
            bad = [str(x.value) for x in b.exception]
            print(("FAIL " if bad else "ok   ")
                  + f"  button {idx} '{label_b[:44]}'"
                  + ("" if not bad else "\n      " + "\n      ".join(bad)))
            if bad:
                failures += 1

    # Sage Actuals with a real statement selected.
    for name in sage_names:
        b = AppTest.from_file(APP, default_timeout=400).run()
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
