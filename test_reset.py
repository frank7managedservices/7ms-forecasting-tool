"""Tests for the forgotten-password flow.

The nasty cases are the ones that matter: a right code paired with a wrong
one must not be spent, and the reset must never let anyone skip the second
step.
"""
import os
import sys
import types

DB = "/tmp/test_reset.db"
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")

PASS = 0
FAIL = 0


def ok(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok   " + label)
    else:
        FAIL += 1
        print("FAIL " + label)


# Create the tables by letting the app boot once.
AppTest.from_file(APP).run()

# Load the app's helpers without tripping the login gate.
H = types.ModuleType("H")
_src = open(APP).read()
_cut = _src.index("require_second_step()\nrequire_login()")
exec(compile(_src[:_cut], APP, "exec"), H.__dict__)


def make_account(login="frank", role="admin", password="adminpassword1",
                 twostep=True, codes=8):
    secret = H.new_totp_secret()
    plain = H.new_recovery_codes(codes) if twostep else []
    row = {
        "login": login,
        "name": login.title(),
        "email": login + "@example.com",
        "role": role,
        "password": H.hash_password(password),
        "must_change": False,
        "totp_secret": secret if twostep else "",
        "totp_active": bool(twostep),
        "totp_last": 0,
        "recovery": [H.hash_recovery(c) for c in plain],
    }
    H.save_users([row])
    return plain


print("=== the codes are checked before any are spent ===")
plain = make_account()
row = H.find_user(H.load_users(), "frank")
ok("two good codes are accepted",
   H.check_two_recovery_codes(row, plain[0], plain[1]))
ok("checking spends nothing",
   len(H.find_user(H.load_users(), "frank")["recovery"]) == 8)
ok("the same code twice is refused",
   not H.check_two_recovery_codes(row, plain[0], plain[0]))
ok("one good and one wrong is refused",
   not H.check_two_recovery_codes(row, plain[0], "AAAA-BBBB-CC"))
ok("two wrong codes are refused",
   not H.check_two_recovery_codes(row, "AAAA-BBBB-CC", "DDDD-EEEE-FF"))
ok("an empty code is refused",
   not H.check_two_recovery_codes(row, plain[0], ""))

print()
print("=== a failed attempt wastes nothing ===")
plain = make_account()
bad, msg = H.reset_password_with_recovery(
    "frank", plain[0], "AAAA-BBBB-CC", "brandnewpassword1")
ok("the reset is refused", not bad)
ok("the good code was NOT burned",
   len(H.find_user(H.load_users(), "frank")["recovery"]) == 8)
ok("the old password still works",
   H.verify_password("adminpassword1",
                     H.find_user(H.load_users(), "frank")["password"]))
ok("the refusal does not say the account exists",
   "frank" not in msg.lower() and "exist" not in msg.lower())

print()
print("=== an unknown username ===")
bad, msg = H.reset_password_with_recovery(
    "nobody", plain[0], plain[1], "brandnewpassword1")
ok("refused", not bad)
ok("worded the same as a wrong code", msg == "Those details are not right.")

print()
print("=== a short new password ===")
bad, msg = H.reset_password_with_recovery(
    "frank", plain[0], plain[1], "short")
ok("refused", not bad)
ok("says why, since this is not a secret", "10 characters" in msg)
ok("no codes were spent",
   len(H.find_user(H.load_users(), "frank")["recovery"]) == 8)

print()
print("=== the happy path ===")
plain = make_account()
good, msg = H.reset_password_with_recovery(
    "frank", plain[0], plain[1], "brandnewpassword1")
ok("the reset succeeds", good)
row = H.find_user(H.load_users(), "frank")
ok("the new password works", H.verify_password("brandnewpassword1",
                                               row["password"]))
ok("the old password is dead", not H.verify_password("adminpassword1",
                                                     row["password"]))
ok("exactly two codes were spent", len(row["recovery"]) == 6)
ok("it says how many are left", "6 recovery codes left" in msg)
ok("no forced change is imposed", row.get("must_change") is False)
ok("two-step is still on", H.twofa_on(row))
ok("the secret was not touched", bool(row.get("totp_secret")))

print()
print("=== the spent codes cannot be used again ===")
bad, _ = H.reset_password_with_recovery(
    "frank", plain[0], plain[1], "anotherpassword12")
ok("the same pair is refused", not bad)
ok("the remaining codes are untouched",
   len(H.find_user(H.load_users(), "frank")["recovery"]) == 6)
ok("a still-unused pair works",
   H.reset_password_with_recovery("frank", plain[2], plain[3],
                                  "anotherpassword12")[0])

print()
print("=== an account with no two-step ===")
make_account(twostep=False, codes=0)
bad, msg = H.reset_password_with_recovery(
    "frank", "AAAA-BBBB-CC", "DDDD-EEEE-FF", "brandnewpassword1")
ok("refused", not bad)
ok("explains to ask an administrator", "administrator" in msg)

print()
print("=== running down to the last codes ===")
plain = make_account(codes=2)
good, msg = H.reset_password_with_recovery(
    "frank", plain[0], plain[1], "brandnewpassword1")
ok("the final pair still works", good)
ok("it reports none left", "0 recovery codes left" in msg)
row = H.find_user(H.load_users(), "frank")
ok("no codes remain", len(row["recovery"]) == 0)
ok("the low-codes warning fires", H.recovery_left_low("frank"))
bad, _ = H.reset_password_with_recovery(
    "frank", plain[0], plain[1], "yetanotherpass12")
ok("with no codes left, reset is refused", not bad)

print()
print("=== the screen itself ===")
plain = make_account()
at = AppTest.from_file(APP, default_timeout=120)
at.run()
labels = [t.label for t in at.text_input]
ok("the sign-in screen offers the reset",
   any("recovery code" in (l or "").lower() for l in labels))
ok("no page menu is showing", len(at.sidebar.radio) == 0)


def press(a, label):
    """Click by label. Index-based clicking picks up the Sign in button."""
    for b in a.button:
        if b.label == label:
            return b.click()
    raise AssertionError("no button " + label)


def field(a, key):
    for t in a.text_input:
        if t.key == key:
            return t
    raise AssertionError("no field " + key)


field(at, "fp_who").set_value("frank")
field(at, "fp_a").set_value(plain[0])
field(at, "fp_b").set_value(plain[1])
field(at, "fp_pw1").set_value("screenpassword1")
field(at, "fp_pw2").set_value("screenpassword1")
press(at, "Set my new password").run()
ok("the screen reports success",
   any("Password changed" in (m.value or "") for m in at.success))
ok("it did NOT sign anyone in", "auth_user" not in at.session_state)
ok("no page menu appeared", len(at.sidebar.radio) == 0)
ok("the database really changed",
   H.verify_password("screenpassword1",
                     H.find_user(H.load_users(), "frank")["password"]))
ok("it tells them the second step still applies",
   any("authenticator" in (m.value or "").lower() for m in at.info))

print()
print("=== mismatched new passwords on the screen ===")
plain = make_account()
at = AppTest.from_file(APP, default_timeout=120)
at.run()
field(at, "fp_who").set_value("frank")
field(at, "fp_a").set_value(plain[0])
field(at, "fp_b").set_value(plain[1])
field(at, "fp_pw1").set_value("screenpassword1")
field(at, "fp_pw2").set_value("screenpassword2")
press(at, "Set my new password").run()
ok("it says they do not match",
   any("do not match" in (m.value or "") for m in at.error))
ok("no codes were spent",
   len(H.find_user(H.load_users(), "frank")["recovery"]) == 8)
ok("the password is unchanged",
   H.verify_password("adminpassword1",
                     H.find_user(H.load_users(), "frank")["password"]))

print()
print("=== the reset is not a way past two-step ===")
plain = make_account()
H.reset_password_with_recovery("frank", plain[0], plain[1], "bypasstest1234")
at = AppTest.from_file(APP, default_timeout=120)
at.run()
# Sign in with the brand new password.
at.text_input[0].set_value("frank")
at.text_input[1].set_value("bypasstest1234")
press(at, "Sign in").run()
ok("the new password is accepted", "auth_half" in at.session_state)
ok("but it stops at the code screen", "auth_user" not in at.session_state)
ok("no page menu yet", len(at.sidebar.radio) == 0)

print()
print("=== nothing leaks on the sign-in screen ===")
blob = str(at._tree)
ok("no password hashes on screen", "pbkdf2" not in blob.lower())
ok("no two-step secret on screen",
   H.find_user(H.load_users(), "frank")["totp_secret"] not in blob)

print()
print(f"{PASS} passed, {FAIL} failed")
print("RESET CLEAN" if FAIL == 0 else "RESET HAS PROBLEMS")
sys.exit(1 if FAIL else 0)
