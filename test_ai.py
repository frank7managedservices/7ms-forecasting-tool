"""Prove the assistant's deterministic half works without any API key."""
import os
import sys
from datetime import date

os.environ["DATABASE_URL"] = os.environ["REAL_DATABASE_URL"]
os.environ.pop("OPENAI_API_KEY", None)
sys.path.insert(0, "/home/user/workspace/7ms")

# app.py stops at the login gate when imported bare, so load only the part
# above it: every helper and loader, none of the page code.
import types

_src = open("/home/user/workspace/7ms/app.py").read()
_cut = _src.index(
    "# --------------------------------------------------------------------------\n"
    "# Accounts and access levels")
A = types.ModuleType("app_helpers")
A.__dict__["__file__"] = "/home/user/workspace/7ms/app.py"
exec(compile(_src[:_cut], "app.py", "exec"), A.__dict__)

fails = []


def ok(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (" " + extra if extra else ""))
    if not cond:
        fails.append(name)


saved = A.load_settings()
sev = A.scheduled_payments(A.load_terminations())
days = max((date.fromisoformat(str(saved["end_date"])[:10])
            - date.today()).days + 1, 30)
exp = A.load_expense_schedule()
rev = A.load_revenue_schedule()
extra = A.load_extra_revenue()
df = A.build_schedule(saved, days, sev, exp, rev, extra)
monthly = A.monthly_summary(df)
ledger, bank = A.load_ledger(), A.load_bank()

print("=== baseline ===")
base_low = float(df["Balance"].min())
base_end = float(df["Balance"].iloc[-1])
print("lowest", base_low, "end", base_end)

# --- facts block -------------------------------------------------------
facts = A.assistant_facts(saved, df, monthly, exp, ledger, bank)
ok("facts has cash", "cash" in facts)
ok("facts lowest matches engine",
   abs(facts["cash"]["lowest_balance"] - round(base_low, 2)) < 0.01,
   str(facts["cash"]["lowest_balance"]))
ok("facts has every month", len(facts["by_month"]) == len(monthly))
ok("facts lists expense lines", len(facts["expense_lines"]) == len(exp))
blob = str(facts)
ok("no employee names leak", "Royal" not in blob and "218353" not in blob)
ok("no password leaks", "npg_" not in blob and "hash" not in blob.lower())

# --- number auditor ---------------------------------------------------
allowed = A.allowed_numbers(facts)
ok("auditor accepts a real figure",
   A.audit_numbers("The low point is " + f"{base_low:,.2f}", allowed) == [])
ok("auditor catches an invention",
   A.audit_numbers("You will be short $884,412.19 in March.", allowed)
   == ["884,412.19"])
ok("auditor ignores years and days",
   A.audit_numbers("On 15 December 2026 across 14 days.", allowed) == [])

# --- scenario: the cut Frank already discussed ------------------------
print("=== scenario: cut Management Fee and Tech from October ===")
changes = [
    {"type": "expense_amount", "line": "Management Fee",
     "new_amount": 8000, "from_month": "2026-10"},
    {"type": "expense_amount", "line": "Tech",
     "new_amount": 1000, "from_month": "2026-10"},
]
ns, ne, nr, did, refused = A.apply_scenario(saved, exp, rev, changes)
ok("both cuts applied", len(did) == 2, str(did))
ok("nothing refused", refused == [], str(refused))
ok("real expense table untouched", len(exp) == 13 and len(ne) == 15)

wi = A.scenario_engine(ns, ne, nr, extra, sev, days)
wi_monthly = A.monthly_summary(wi)
new_low = float(wi["Balance"].min())
print("scenario lowest", new_low, "improvement", new_low - base_low)
ok("cut improves the low point", new_low > base_low,
   f"{base_low:,.2f} -> {new_low:,.2f}")

# September must be untouched, October must be lower.
def other_fixed(frame, month):
    r = frame[frame["Month"] == month]
    return float(r["Other Fixed"].iloc[0]) if not r.empty else 0.0


sep_before, sep_after = other_fixed(monthly, "2026-09"), other_fixed(wi_monthly, "2026-09")
oct_before, oct_after = other_fixed(monthly, "2026-10"), other_fixed(wi_monthly, "2026-10")
ok("September unchanged by an October cut",
   abs(sep_before - sep_after) < 0.01, f"{sep_before:,.2f}")
ok("October falls by 13,385",
   abs((oct_before - oct_after) - 13385.0) < 1.0,
   f"{oct_before:,.2f} -> {oct_after:,.2f}")

cmp_frame = A.compare_plans(monthly, wi_monthly)
ok("comparison covers every month", len(cmp_frame) == len(monthly))
ok("comparison improvement is positive by December",
   float(cmp_frame.iloc[-1]["Improvement"]) > 0,
   f"{float(cmp_frame.iloc[-1]['Improvement']):,.2f}")

# --- scenario: stop a line, add a line, change a setting --------------
ns2, ne2, nr2, did2, ref2 = A.apply_scenario(saved, exp, rev, [
    {"type": "expense_stop", "line": "supplies", "from_month": "2026-11"},
    {"type": "expense_add", "line": "New software", "new_amount": 1200,
     "due_day": 15, "from_month": "2026-09", "flexible": True},
    {"type": "setting", "name": "payroll", "value": 150000},
])
ok("stop, add and setting all applied", len(did2) == 3, str(did2))
wi2 = A.scenario_engine(ns2, ne2, nr2, extra, sev, days)
ok("stopped line is gone by November",
   abs(other_fixed(A.monthly_summary(wi2), "2026-11")
       - (other_fixed(monthly, "2026-11") - 2000 + 1200)) < 1.0)

# --- refusals ---------------------------------------------------------
_, _, _, did3, ref3 = A.apply_scenario(saved, exp, rev, [
    {"type": "expense_amount", "line": "Yacht mooring", "new_amount": 500},
    {"type": "setting", "name": "loc_drawn", "value": 0},
    {"type": "wire_money", "to": "somewhere"},
])
ok("invented expense line refused", any("Yacht" in r for r in ref3))
ok("off-limits setting refused", any("loc_drawn" in r for r in ref3))
ok("unknown change type refused", any("wire_money" in r for r in ref3))
ok("nothing was applied from bad input", did3 == [], str(did3))

# --- gap analysis -----------------------------------------------------
gaps, a, b = A.gap_candidates(saved, df, ledger, bank)
ok("gap analysis runs without crashing", isinstance(gaps, type(exp)))
print("bank rows:", len(bank), "gap rows:", len(gaps), "window", a, "to", b)

# --- no key means graceful, not broken --------------------------------
txt, err = A.ask_model("system", "user")
ok("no key gives a clear message not a crash",
   txt is None and "No API key" in err, str(err))
ok("model name is a setting", A.assistant_model() == A.DEFAULT_MODEL)

print()
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("ASSISTANT LOGIC CLEAN")
