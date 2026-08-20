"""The full two-step journey, driven through the real app.

Covers enrolment, the second step at sign-in, recovery codes, replay, the
compulsory setting, and the admin rescue. Nothing here is mocked except the
clock, which has to be to test drift.
"""
import json
import os
import sqlite3
import sys
import time
import types

sys.path.insert(0, "/home/user/workspace/7ms")
import test_pages as T                                     # noqa: E402
from streamlit.testing.v1 import AppTest                    # noqa: E402

APP = "/home/user/workspace/7ms/app.py"
fails = []


def ok(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ("  " + extra if extra
                                                   and not cond else ""))
    if not cond:
        fails.append(name)


# Load the helpers on their own so we can generate valid codes in the test.
_src = open(APP).read()
_cut = _src.index("PAGES = [")
H = types.ModuleType("h")
H.__dict__["__file__"] = APP
exec(compile(_src[:_src.index("require_second_step()\nrequire_login()")],
             APP, "exec"), H.__dict__)


def users_in_db():
    con = sqlite3.connect(T.DB)
    row = con.execute("select body from app_settings where name='users'"
                      ).fetchone()
    con.close()
    return json.loads(row[0]) if row else []


def set_users(rows):
    con = sqlite3.connect(T.DB)
    con.execute("insert or replace into app_settings(name, body, saved_at)"
                " values(?,?,datetime('now'))", ("users", json.dumps(rows)))
    con.commit()
    con.close()


def set_flag(name, value):
    con = sqlite3.connect(T.DB)
    con.execute("insert or replace into app_settings(name, body, saved_at)"
                " values(?,?,datetime('now'))", (name, json.dumps(value)))
    con.commit()
    con.close()


def fresh():
    """A clean app with the three standard accounts and no two-step."""
    T.seed_users()
    set_flag("require_2fa", False)
    return AppTest.from_file(APP, default_timeout=240)


def code_for(secret):
    return H.totp_now(secret)


def next_window():
    """Cross into the next 30-second slot, so the code is genuinely new."""
    now = time.time()
    time.sleep(H.TOTP_STEP - (now % H.TOTP_STEP) + 1.0)


# The app builds its own tables on first run, so let it.
AppTest.from_file(APP, default_timeout=400).run()
T.copy_real_data()
T.seed_daily_log()

print("=== enrolment ===")
at = fresh().run()
at.text_input[0].set_value("frank").run()
at.text_input[1].set_value("adminpassword1").run()
at.button[0].click().run()
ok("password only still signs in when two-step is off",
   not at.exception and any("Go to" in str(r.label) for r in at.sidebar.radio))

at.sidebar.radio[0].set_value("My Account").run()
ok("My Account page opens", not at.exception)
ok("page offers to set two-step up",
   any("Start setting it up" in str(b.label) for b in at.button))

for b in at.button:
    if "Start setting it up" in str(b.label):
        b.click().run()
        break
secret = None
for c in at.code:
    v = str(c.value).strip()
    if len(v) == 32 and v.isalnum() and v.isupper():
        secret = v
ok("a setup key is shown", bool(secret), str([str(c.value)[:40]
                                              for c in at.code]))
ok("nothing saved to the account before the code is proved",
   not any(u.get("totp_secret") for u in users_in_db()))

# A wrong code must not enrol.
at.text_input[0].set_value("000000").run()
at.button[0].click().run()
still_off = not any(u.get("totp_active") for u in users_in_db())
ok("a wrong code does not switch two-step on", still_off)

# The right code must.
at = fresh().run()
at.text_input[0].set_value("frank").run()
at.text_input[1].set_value("adminpassword1").run()
at.button[0].click().run()
at.sidebar.radio[0].set_value("My Account").run()
for b in at.button:
    if "Start setting it up" in str(b.label):
        b.click().run()
        break
secret = None
for c in at.code:
    v = str(c.value).strip()
    if len(v) == 32 and v.isalnum():
        secret = v
at.text_input[0].set_value(code_for(secret)).run()
for b in at.button:
    if "Turn on two-step" in str(b.label):
        b.click().run()
        break
rows = users_in_db()
me = [u for u in rows if u["login"] == "frank"][0]
ok("the right code switches two-step on", bool(me.get("totp_active")))
ok("a secret is stored", me.get("totp_secret") == secret)
ok("recovery codes were issued",
   len(me.get("recovery") or []) == H.RECOVERY_CODES,
   str(len(me.get("recovery") or [])))
ok("recovery codes are stored hashed, not readable",
   all("$" in c and len(c) > 60 for c in me.get("recovery")))
shown = [str(c.value) for c in at.code]
ok("the codes are shown to the user once",
   any(len(s.splitlines()) == H.RECOVERY_CODES for s in shown))
plain = [s for s in shown if len(s.splitlines()) == H.RECOVERY_CODES][0]
plain_codes = plain.splitlines()
ok("shown codes match the stored hashes",
   H.verify_password(H.normalise_recovery(plain_codes[0]),
                     me["recovery"][0]))

print()
print("=== the second step at sign-in ===")
at = AppTest.from_file(APP, default_timeout=240).run()
at.text_input[0].set_value("frank").run()
at.text_input[1].set_value("adminpassword1").run()
at.button[0].click().run()
asked = any("Enter your code" in str(h.value) for h in at.subheader)
ok("correct password alone no longer gets in", asked,
   str([str(h.value) for h in at.subheader]))
ok("no page menu is visible at the code screen",
   not any("Go to" in str(r.label) for r in at.sidebar.radio))

at.text_input[0].set_value("000000").run()
at.button[0].click().run()
ok("a wrong code is refused",
   any("not accepted" in str(e.value) for e in at.error))
ok("still no page menu after a wrong code",
   not any("Go to" in str(r.label) for r in at.sidebar.radio))

next_window()
good = code_for(secret)
at.text_input[0].set_value(good).run()
at.button[0].click().run()
ok("the right code gets in",
   not at.exception and any("Go to" in str(r.label)
                            for r in at.sidebar.radio))

print()
print("=== a used code cannot be used again ===")
at2 = AppTest.from_file(APP, default_timeout=240).run()
at2.text_input[0].set_value("frank").run()
at2.text_input[1].set_value("adminpassword1").run()
at2.button[0].click().run()
at2.text_input[0].set_value(good).run()
at2.button[0].click().run()
blocked = any("not accepted" in str(e.value) for e in at2.error)
ok("the same code is refused the second time", blocked)

print()
print("=== wrong password with two-step on ===")
at3 = AppTest.from_file(APP, default_timeout=240).run()
at3.text_input[0].set_value("frank").run()
at3.text_input[1].set_value("wrongpassword").run()
at3.button[0].click().run()
ok("a wrong password never reaches the code screen",
   not any("Enter your code" in str(h.value) for h in at3.subheader)
   and any("not right" in str(e.value) for e in at3.error))

print()
print("=== recovery code ===")
at4 = AppTest.from_file(APP, default_timeout=240).run()
at4.text_input[0].set_value("frank").run()
at4.text_input[1].set_value("adminpassword1").run()
at4.button[0].click().run()
# The recovery form lives in the expander, after the code form.
at4.text_input[1].set_value(plain_codes[3]).run()
for b in at4.button:
    if "Use this code" in str(b.label):
        b.click().run()
        break
got_in = any("Go to" in str(r.label) for r in at4.sidebar.radio)
ok("a recovery code signs you in", got_in)
left = len([u for u in users_in_db() if u["login"] == "frank"][0]["recovery"])
ok("the used recovery code is consumed",
   left == H.RECOVERY_CODES - 1, str(left))

at5 = AppTest.from_file(APP, default_timeout=240).run()
at5.text_input[0].set_value("frank").run()
at5.text_input[1].set_value("adminpassword1").run()
at5.button[0].click().run()
at5.text_input[1].set_value(plain_codes[3]).run()
for b in at5.button:
    if "Use this code" in str(b.label):
        b.click().run()
        break
ok("the same recovery code cannot be reused",
   not any("Go to" in str(r.label) for r in at5.sidebar.radio))

print()
print("=== admin rescue ===")
at6 = AppTest.from_file(APP, default_timeout=240)
at6.session_state["auth_user"] = {"login": "frank", "name": "Frank",
                                  "email": "", "role": "admin",
                                  "must_change": False}
at6.run()
at6.sidebar.radio[0].set_value("Accounts").run()
ok("Accounts page shows who has two-step on", not at6.exception)
for cb in at6.checkbox:
    if "confirmed who this is" in str(cb.label):
        cb.set_value(True).run()
        break
for b in at6.button:
    if "Switch two-step off" in str(b.label):
        b.click().run()
        break
me = [u for u in users_in_db() if u["login"] == "frank"][0]
ok("admin can switch two-step off for a locked-out account",
   not me.get("totp_active") and not me.get("totp_secret"))
ok("the recovery codes go with it", not me.get("recovery"))

at7 = AppTest.from_file(APP, default_timeout=240).run()
at7.text_input[0].set_value("frank").run()
at7.text_input[1].set_value("adminpassword1").run()
at7.button[0].click().run()
ok("password alone works again after the rescue",
   any("Go to" in str(r.label) for r in at7.sidebar.radio))

print()
print("=== compulsory two-step ===")
T.seed_users()
set_flag("require_2fa", True)
at8 = AppTest.from_file(APP, default_timeout=240).run()
at8.text_input[0].set_value("clerk").run()
at8.text_input[1].set_value("userpassword1").run()
at8.button[0].click().run()
forced = any("Two-step sign-in is required" in str(t.value)
             for t in at8.title)
ok("a user without two-step is made to set it up", forced,
   str([str(t.value) for t in at8.title]))
ok("no page menu until they do",
   not any("Go to" in str(r.label) for r in at8.sidebar.radio))
ok("the setup offer is on that screen",
   any("Start setting it up" in str(b.label) for b in at8.button))

set_flag("require_2fa", False)

print()
print("=== nothing leaks ===")
at9 = AppTest.from_file(APP, default_timeout=240)
at9.session_state["auth_user"] = {"login": "frank", "name": "Frank",
                                  "email": "", "role": "admin",
                                  "must_change": False}
at9.run()
at9.sidebar.radio[0].set_value("Accounts").run()
blob = str([str(d.value) for d in at9.dataframe])
ok("the accounts table shows no password hashes",
   "$" not in blob or "pbkdf" not in blob.lower())
ok("the accounts table shows no two-step secrets",
   not any(len(w) == 32 and w.isalnum() and w.isupper()
           for w in blob.replace("'", " ").split()))

print()
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("TWO-STEP CLEAN")
