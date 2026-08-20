import calendar
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

import payroll
from payroll import read_payroll, summarize, by_group, loan_detail

st.set_page_config(page_title="7MS Forecasting Tool", page_icon="📈", layout="wide")

SETTINGS_FILE = Path("cash_settings.json")

DEFAULTS = {
    "start_cash": 0.0,
    "revenue": 0.0,
    "revenue_mode": "Spread evenly",
    "revenue_lag": 0,
    "revenue_day": 20,
    "horizon": 120,
    "end_date": str(date(date.today().year, 12, 31)),
    "basis": "Cash - pay when it comes due",
    "expense_mode": "Spread evenly",
    "decimo_provision": 0.0,
    "vacation_provision": 0.0,
    "payroll": 0.0,
    "css": 0.0,
    "pluxee": 0.0,
    "viatico": 0.0,
    "decimo": 0.0,
    "fixed": 0.0,
    "loc_limit": 0.0,
    "loc_drawn": 0.0,
    "loc_rate": 0.0,
    "loc_auto": True,
    "loc_min_cash": 0.0,
    "loc_draw_amount": 0.0,
    "loc_draw_day": str(date.today()),
}

MONEY = "${:,.2f}"

# ---------------------------------------------------------------------------
# Persistent storage.
# Streamlit's own disk is wiped on every restart, so anything worth keeping
# goes to a Postgres database. The connection string comes from the
# DATABASE_URL secret and never appears in this repository. If no secret is
# set the app silently falls back to local JSON files, exactly as before, so
# it still runs for anyone without a database.
# ---------------------------------------------------------------------------

import io
import os

from sqlalchemy import create_engine, text


def db_url():
    try:
        if "DATABASE_URL" in st.secrets:
            return str(st.secrets["DATABASE_URL"]).strip()
    except Exception:
        pass
    return (os.environ.get("DATABASE_URL") or "").strip()


@st.cache_resource(show_spinner=False)
def get_engine(url):
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=280)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS app_settings ("
            " name VARCHAR(120) PRIMARY KEY,"
            " body TEXT NOT NULL,"
            " saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS app_documents ("
            " kind VARCHAR(40) NOT NULL,"
            " name VARCHAR(200) NOT NULL,"
            " body TEXT NOT NULL,"
            " notes TEXT,"
            " saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            " PRIMARY KEY (kind, name))"
        ))
    return engine


def db():
    """Return a live engine, or None if storage is not configured."""
    url = db_url()
    if not url:
        return None
    try:
        return get_engine(url)
    except Exception as exc:
        st.session_state["db_error"] = str(exc)
        return None


def db_ready():
    return db() is not None


def kv_put(name, obj):
    engine = db()
    if engine is None:
        return False
    body = json.dumps(obj, default=str)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM app_settings WHERE name = :n"), {"n": name})
        conn.execute(
            text("INSERT INTO app_settings (name, body) VALUES (:n, :b)"),
            {"n": name, "b": body},
        )
    return True


def kv_get(name, default=None):
    engine = db()
    if engine is None:
        return default
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT body FROM app_settings WHERE name = :n"), {"n": name}
            ).fetchone()
    except Exception:
        return default
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return default


def doc_put(kind, name, frame, notes=""):
    """Save a dataframe under a name you can pick from a list later."""
    engine = db()
    if engine is None:
        return False
    body = frame.to_csv(index=False)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM app_documents WHERE kind = :k AND name = :n"),
            {"k": kind, "n": name},
        )
        conn.execute(
            text("INSERT INTO app_documents (kind, name, body, notes)"
                 " VALUES (:k, :n, :b, :o)"),
            {"k": kind, "n": name, "b": body, "o": notes},
        )
    return True


def doc_list(kind):
    engine = db()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT name, notes, saved_at, LENGTH(body) FROM app_documents"
                     " WHERE kind = :k ORDER BY name"),
                {"k": kind},
            ).fetchall()
    except Exception:
        return []
    return [{"name": r[0], "notes": r[1] or "", "saved_at": r[2], "size": r[3]}
            for r in rows]


def doc_get(kind, name, dates=None):
    engine = db()
    if engine is None:
        return None
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT body FROM app_documents WHERE kind = :k AND name = :n"),
            {"k": kind, "n": name},
        ).fetchone()
    if row is None:
        return None
    return pd.read_csv(io.StringIO(row[0]), parse_dates=dates or [])


def doc_delete(kind, name):
    engine = db()
    if engine is None:
        return False
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM app_documents WHERE kind = :k AND name = :n"),
            {"k": kind, "n": name},
        )
    return True


def raw_put(kind, name, body, notes=""):
    engine = db()
    if engine is None:
        return False
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM app_documents WHERE kind = :k AND name = :n"),
            {"k": kind, "n": name},
        )
        conn.execute(
            text("INSERT INTO app_documents (kind, name, body, notes)"
                 " VALUES (:k, :n, :b, :o)"),
            {"k": kind, "n": name, "b": body, "o": notes},
        )
    return True


def raw_get(kind, name):
    engine = db()
    if engine is None:
        return None
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT body FROM app_documents WHERE kind = :k AND name = :n"),
            {"k": kind, "n": name},
        ).fetchone()
    return None if row is None else row[0]


def sheet_text(file, name):
    """Turn an uploaded sheet into plain CSV text we can store and re-parse."""
    return _read_any(file, name).to_csv(index=False, header=False)


def stored_picker(kind, label, help_text):
    """Return (choice, names) for a saved-document selector."""
    rows = doc_list(kind)
    names = [r["name"] for r in rows]
    if not names:
        st.caption(help_text)
        return "Upload a new file", rows
    return st.selectbox(label, ["Upload a new file"] + names,
                        key=f"pick_{kind}"), rows


def storage_note():
    """One line in the sidebar so you always know where data is going."""
    if db_ready():
        st.sidebar.success("Saved data: database")
    else:
        st.sidebar.warning("Saved data: this session only")
        if st.session_state.get("db_error"):
            st.sidebar.caption("Database not reachable. Using temporary files.")


# ---------------------------------------------------------------------------
# Panama liquidacion (severance), per the Codigo de Trabajo de Panama:
#   Weekly salary = monthly x 12 / 52.   Daily salary = monthly / 30.
#   Prima de antiguedad (Art. 224): 1 week of salary per year of service, owed
#     on any indefinite contract regardless of cause; partial years prorated.
#   Indemnizacion (Art. 225): 3.4 weeks per year for the first 10 years, then
#     1 week per year from year 11. Only for despido injustificado.
#   Decimo proporcional: accrued since the last decimo payment (15 Apr/Aug/Dec).
#   Vacaciones proporcionales: 30 days of vacation per 11 months worked.
#   Payment is scheduled 30 days after the termination date.
# These are estimates for cash planning; the final figure comes from payroll.
# ---------------------------------------------------------------------------

TERMINATIONS_FILE = Path("terminations.json")

PAY_DELAY_DAYS = 30
DECIMO_DAYS = [(4, 15), (8, 15), (12, 15)]

REASONS = [
    "Despido injustificado",
    "Despido justificado",
    "Renuncia",
    "Mutuo acuerdo",
    "Jubilacion",
]

# Indemnizacion under Art. 225 applies only to unjustified dismissal.
INDEMNITY_REASONS = {"Despido injustificado"}


def weekly_salary(monthly):
    return monthly * 12 / 52


def daily_salary(monthly):
    return monthly / 30


def years_of_service(hire, term):
    return max((term - hire).days, 0) / 365


def last_decimo_date(term):
    """Most recent 15 April / 15 August / 15 December on or before the date."""
    candidates = []
    for year in (term.year - 1, term.year):
        for month, day in DECIMO_DAYS:
            d = date(year, month, day)
            if d <= term:
                candidates.append(d)
    return max(candidates) if candidates else term


def decimo_proportional(monthly, term, since=None):
    since = since or last_decimo_date(term)
    months = max((term - since).days, 0) / 30
    return monthly * months / 12


def vacation_days_accrued(monthly, term, since):
    """30 days of vacation per 11 months worked."""
    months = max((term - since).days, 0) / 30
    return months * 30 / 11


def estimate(monthly, hire, term, reason, decimo_since=None,
             vacation_since=None, vacation_days=None):
    """Return every liquidacion component plus the scheduled payment date."""
    week = weekly_salary(monthly)
    day = daily_salary(monthly)
    years = years_of_service(hire, term)

    prima = week * years

    if reason in INDEMNITY_REASONS:
        if years <= 10:
            indemnity_weeks = 3.4 * years
        else:
            indemnity_weeks = 3.4 * 10 + (years - 10)
        indemnity = week * indemnity_weeks
    else:
        indemnity = 0.0

    decimo = decimo_proportional(monthly, term, decimo_since)

    if vacation_days is None:
        base = vacation_since or hire
        vacation_days = vacation_days_accrued(monthly, term, base)
    vacation = vacation_days * day

    total = prima + indemnity + decimo + vacation

    return {
        "weekly_salary": round(week, 2),
        "daily_salary": round(day, 2),
        "years_of_service": round(years, 2),
        "prima_antiguedad": round(prima, 2),
        "indemnizacion": round(indemnity, 2),
        "decimo_proporcional": round(decimo, 2),
        "vacation_days": round(vacation_days, 2),
        "vacaciones_proporcionales": round(vacation, 2),
        "total": round(total, 2),
        "payment_date": term + timedelta(days=PAY_DELAY_DAYS),
    }


def load_terminations():
    stored = kv_get("terminations")
    if isinstance(stored, list):
        return stored
    if TERMINATIONS_FILE.exists():
        try:
            return json.loads(TERMINATIONS_FILE.read_text())
        except Exception:
            return []
    return []


def save_terminations(records):
    if not kv_put("terminations", records):
        TERMINATIONS_FILE.write_text(json.dumps(records, indent=2, default=str))


def add_termination(record):
    records = load_terminations()
    records.append(record)
    save_terminations(records)
    return records


def scheduled_payments(records):
    """Map of payment date -> total amount due, for cash flow use."""
    out = {}
    for r in records:
        if r.get("paid"):
            continue
        try:
            pay_date = date.fromisoformat(str(r["payment_date"])[:10])
        except Exception:
            continue
        out[pay_date] = out.get(pay_date, 0.0) + float(r.get("total", 0.0))
    return out


def load_settings():
    stored = kv_get("cash_settings")
    if isinstance(stored, dict):
        return {**DEFAULTS, **stored}
    if SETTINGS_FILE.exists():
        try:
            return {**DEFAULTS, **json.loads(SETTINGS_FILE.read_text())}
        except Exception:
            return dict(DEFAULTS)
    return dict(DEFAULTS)


def save_settings(values):
    if not kv_put("cash_settings", values):
        SETTINGS_FILE.write_text(json.dumps(values, indent=2))


# ---------------------------------------------------------------------------
# Two schedules you can edit and keep: what you collect each month, and what
# each expense costs and the day of the month it actually leaves the bank.
# ---------------------------------------------------------------------------

EXPENSE_COLUMNS = ["Expense", "Monthly amount", "Day of month",
                   "Starts", "Ends", "Flexible"]
REVENUE_COLUMNS = ["Month", "Expected collections", "Day of month"]


def load_expense_schedule():
    """Expense lines, widened for effective dates and payment flexibility.

    Rows saved before those columns existed come back with blank dates, which
    means always active, and Flexible false, which means never move it.
    """
    rows = kv_get("expense_schedule")
    if isinstance(rows, list) and rows:
        frame = pd.DataFrame(rows)
        for c in ("Starts", "Ends"):
            if c not in frame.columns:
                frame[c] = ""
            frame[c] = frame[c].fillna("").astype(str).str.strip()
        if "Flexible" not in frame.columns:
            frame["Flexible"] = False
        frame["Flexible"] = frame["Flexible"].fillna(False).astype(bool)
        return frame[EXPENSE_COLUMNS]
    return pd.DataFrame(columns=EXPENSE_COLUMNS)


def save_expense_schedule(frame):
    frame = frame.dropna(subset=["Expense"])
    frame = frame[frame["Expense"].astype(str).str.strip() != ""].copy()
    for c in ("Starts", "Ends"):
        if c not in frame.columns:
            frame[c] = ""
        frame[c] = frame[c].fillna("").astype(str).str.strip()
    if "Flexible" not in frame.columns:
        frame["Flexible"] = False
    frame["Flexible"] = frame["Flexible"].fillna(False).astype(bool)
    kv_put("expense_schedule", frame[EXPENSE_COLUMNS].to_dict("records"))
    return frame


def month_label(d):
    return f"{d.year:04d}-{d.month:02d}"


def normalise_month(value):
    """Accept 2026-10, 2026/10, 10-2026, or a full date. Blank means open."""
    text = str(value or "").strip()
    if not text or text.lower() in ("nan", "none", "nat"):
        return ""
    text = text.replace("/", "-")
    parts = [x for x in text.split("-") if x]
    try:
        if len(parts) >= 2 and len(parts[0]) == 4:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}"
        if len(parts) >= 2 and len(parts[-1]) == 4:
            return f"{int(parts[-1]):04d}-{int(parts[0]):02d}"
    except Exception:
        return ""
    return ""


def expense_active(row, label):
    """Is this line in force in the given YYYY-MM month?"""
    starts = normalise_month(row.get("Starts", ""))
    ends = normalise_month(row.get("Ends", ""))
    if starts and label < starts:
        return False
    if ends and label > ends:
        return False
    return True


def expense_due_map(expenses, label):
    """Day of month to amount, for the lines in force that month.

    Returns two dicts: everything due, and the flexible part of it.
    """
    due, flex = {}, {}
    if expenses is None or expenses.empty:
        return due, flex
    for _, r in expenses.iterrows():
        if not expense_active(r, label):
            continue
        try:
            day = max(1, min(int(r["Day of month"]), 31))
            amount = float(r["Monthly amount"])
        except Exception:
            continue
        due[day] = due.get(day, 0.0) + amount
        if bool(r.get("Flexible", False)):
            flex[day] = flex.get(day, 0.0) + amount
    return due, flex


def flexible_between(expenses, start, end):
    """Flexible lines falling due between two dates, with their dates."""
    rows = []
    if expenses is None or expenses.empty:
        return pd.DataFrame(columns=["Date", "Expense", "Amount"])
    d = start
    while d <= end:
        label = month_label(d)
        dim = calendar.monthrange(d.year, d.month)[1]
        for _, r in expenses.iterrows():
            if not expense_active(r, label):
                continue
            if not bool(r.get("Flexible", False)):
                continue
            try:
                day = max(1, min(int(r["Day of month"]), 31))
                amount = float(r["Monthly amount"])
            except Exception:
                continue
            lands = min(day, dim)
            if lands == d.day:
                rows.append({"Date": d.isoformat(),
                             "Expense": str(r["Expense"]),
                             "Amount": amount})
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def load_revenue_schedule():
    rows = kv_get("revenue_schedule")
    if isinstance(rows, list) and rows:
        return pd.DataFrame(rows)[REVENUE_COLUMNS]
    return pd.DataFrame(columns=REVENUE_COLUMNS)


def save_revenue_schedule(frame):
    frame = frame.dropna(subset=["Month"])
    frame = frame[frame["Month"].astype(str).str.strip() != ""]
    kv_put("revenue_schedule", frame.to_dict("records"))
    return frame


EXTRA_REVENUE_COLUMNS = ["Stream", "Month", "Expected amount", "Day of month"]


def load_extra_revenue():
    rows = kv_get("extra_revenue")
    if isinstance(rows, list) and rows:
        frame = pd.DataFrame(rows)
        for c in EXTRA_REVENUE_COLUMNS:
            if c not in frame.columns:
                frame[c] = 0
        return frame[EXTRA_REVENUE_COLUMNS]
    return pd.DataFrame(columns=EXTRA_REVENUE_COLUMNS)


def save_extra_revenue(frame):
    frame = frame.dropna(subset=["Month"])
    frame = frame[frame["Month"].astype(str).str.strip() != ""]
    kv_put("extra_revenue", frame.to_dict("records"))
    return frame


def extra_revenue_by_day(frame):
    """Map each extra revenue row to the month and day it is collected."""
    plan = {}
    if frame is None or frame.empty:
        return plan
    for _, r in frame.iterrows():
        try:
            month = str(r["Month"]).strip()
            amount = float(r["Expected amount"])
            day = int(r["Day of month"])
        except Exception:
            continue
        if amount:
            plan[(month, max(1, min(day, 31)))] = (
                plan.get((month, max(1, min(day, 31))), 0.0) + amount)
    return plan


def extra_revenue_totals(frame):
    """Total per stream, so each line can be seen on its own."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["Stream", "Total"])
    out = frame.copy()
    out["Expected amount"] = pd.to_numeric(out["Expected amount"],
                                           errors="coerce").fillna(0.0)
    return (out.groupby("Stream", as_index=False)["Expected amount"].sum()
            .rename(columns={"Expected amount": "Total"})
            .sort_values("Total", ascending=False))


AGENT_COLUMNS = ["Month", "Agents", "Billable hours per agent",
                 "Average rate per hour"]


def load_agent_schedule():
    rows = kv_get("agent_schedule")
    if isinstance(rows, list) and rows:
        frame = pd.DataFrame(rows)
        for c in AGENT_COLUMNS:
            if c not in frame.columns:
                frame[c] = 0
        return frame[AGENT_COLUMNS]
    return pd.DataFrame(columns=AGENT_COLUMNS)


def save_agent_schedule(frame):
    frame = frame.dropna(subset=["Month"])
    frame = frame[frame["Month"].astype(str).str.strip() != ""]
    kv_put("agent_schedule", frame.to_dict("records"))
    return frame


def agent_billing(frame):
    """Agents times billable hours times rate, per month."""
    out = frame.copy()
    for c in ["Agents", "Billable hours per agent", "Average rate per hour"]:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    out["Billed"] = (out["Agents"] * out["Billable hours per agent"]
                     * out["Average rate per hour"])
    return out


def shift_month(label, months):
    """Move a YYYY-MM label forward by a number of months."""
    y, m = int(str(label)[:4]), int(str(label)[5:7])
    total = y * 12 + (m - 1) + int(months)
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def agent_revenue_schedule(frame, day, lag):
    """Turn the agent plan into collections, allowing for a payment lag.

    Work billed in one month is often collected in the next, so the lag moves
    each month's billing forward before it lands as cash.
    """
    billed = agent_billing(frame)
    return pd.DataFrame([
        {"Month": shift_month(r["Month"], lag),
         "Expected collections": float(r["Billed"]),
         "Day of month": int(day)}
        for _, r in billed.iterrows()
    ])


def months_between(start, end):
    """List of YYYY-MM labels covering the projection window."""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def blank_revenue_schedule(start, end, amount, day):
    return pd.DataFrame([
        {"Month": label, "Expected collections": float(amount),
         "Day of month": int(day)}
        for label in months_between(start, end)
    ])


def days_in_month(d):
    return calendar.monthrange(d.year, d.month)[1]


def is_last_day(d):
    return d.day == days_in_month(d)


def build_schedule(s, horizon, severance=None, expenses=None, revenues=None,
                   extra_revenue=None):
    """Day-by-day cash projection on either a cash or an accrual basis.

    Cash basis: money leaves on the day it actually leaves. Decimo hits on
    15 April, 15 August and 15 December. Vacation is not charged monthly; it
    only appears when someone is actually paid out.

    Accrual basis: decimo and vacation are charged as smooth monthly
    provisions, the way the books carry them, and the three decimo payments
    are not charged again since they were already provisioned. Over a full
    year both bases move the same total, they just distribute it differently.

    Expenses can either be spread evenly across each month or taken from a
    schedule where every line has its own due day. Collections can be one
    figure every month or a different figure per month.

    The line of credit tops cash back up to your floor whenever a day would
    otherwise fall below it, and a one-time draw lands on the date you pick.
    """
    severance = severance or {}
    start = date.today()
    balance = s["start_cash"]

    accrual = str(s.get("basis", "")).lower().startswith("accrual")

    decimo_provision = float(s.get("decimo_provision", 0.0))
    vacation_provision = float(s.get("vacation_provision", 0.0))

    use_expense_schedule = (
        str(s.get("expense_mode", "")).lower().startswith("use")
        and expenses is not None and not expenses.empty
    )
    # Expense lines can start and stop, so the map of what is due has to be
    # worked out per month rather than once for the whole projection. A cost
    # cut taking effect in October must not reduce August.
    due_cache = {}

    def due_for(d):
        key = (d.year, d.month)
        if key not in due_cache:
            due_cache[key] = expense_due_map(expenses, month_label(d))
        return due_cache[key]

    mode_word = str(s.get("revenue_mode", "")).lower()
    by_month = (
        (mode_word.startswith("enter") or mode_word.startswith("build"))
        and revenues is not None and not revenues.empty
    )
    if by_month:
        plan = {}
        for _, r in revenues.iterrows():
            try:
                plan[str(r["Month"]).strip()] = (
                    float(r["Expected collections"]), int(r["Day of month"]))
            except Exception:
                continue

    extra_plan = extra_revenue_by_day(extra_revenue)

    loc_limit = float(s.get("loc_limit", 0.0))
    loc_balance = float(s.get("loc_drawn", 0.0))
    loc_rate = float(s.get("loc_rate", 0.0)) / 100.0
    loc_auto = bool(s.get("loc_auto", False))
    loc_min_cash = float(s.get("loc_min_cash", 0.0))
    loc_draw_amount = float(s.get("loc_draw_amount", 0.0))
    try:
        loc_draw_day = date.fromisoformat(str(s.get("loc_draw_day"))[:10])
    except Exception:
        loc_draw_day = None

    rows = []
    for i in range(horizon):
        d = start + timedelta(days=i)
        dim = days_in_month(d)
        label = f"{d.year:04d}-{d.month:02d}"

        if by_month:
            amount, day = plan.get(label, (0.0, int(s.get("revenue_day", 20))))
            collections = amount if d.day == min(day, dim) else 0.0
        elif s["revenue_mode"] == "Spread evenly":
            collections = s["revenue"] / dim
        else:
            collections = s["revenue"] if d.day == min(s["revenue_day"], dim) else 0.0

        # Software and anything else outside the core book lands on its own day.
        other_revenue = extra_plan.get((label, d.day), 0.0)
        if d.day == dim:
            other_revenue += sum(v for (m, k), v in extra_plan.items()
                                 if m == label and k > dim)

        pay = s["payroll"] / 2 if (d.day == 15 or is_last_day(d)) else 0.0
        css_out = s["css"] if is_last_day(d) else 0.0
        pluxee_out = s["pluxee"] if d.day == 15 else 0.0
        viatico_out = s["viatico"] if d.day == 15 else 0.0
        sev_out = severance.get(d, 0.0)

        if accrual:
            # Provisioned monthly at month end, so the lump payments are not
            # charged a second time.
            decimo_out = 0.0
            provision = (decimo_provision + vacation_provision) if is_last_day(d) else 0.0
        else:
            decimo_out = (s.get("decimo", 0.0)
                          if (d.day == 15 and d.month in (4, 8, 12)) else 0.0)
            provision = 0.0

        if use_expense_schedule:
            due, _flex = due_for(d)
            fixed_out = due.get(min(d.day, dim), 0.0)
            # Anything due after the last day of a short month lands on that day.
            if d.day == dim:
                fixed_out += sum(v for k, v in due.items() if k > dim)
        else:
            fixed_out = s["fixed"] / dim

        interest = loc_balance * loc_rate / 12 if (is_last_day(d) and loc_rate) else 0.0

        out = (pay + css_out + pluxee_out + viatico_out + decimo_out + provision
               + sev_out + fixed_out + interest)

        draw = 0.0
        available = max(loc_limit - loc_balance, 0.0)
        if loc_draw_day is not None and d == loc_draw_day and loc_draw_amount > 0:
            draw += min(loc_draw_amount, available)
            available -= draw

        projected = balance + collections + other_revenue - out + draw
        if loc_auto and available > 0 and projected < loc_min_cash:
            need = loc_min_cash - projected
            # Draw in round thousands, the way a bank transfer actually happens.
            need = -(-need // 1000) * 1000
            extra = min(need, available)
            draw += extra
            available -= extra

        loc_balance += draw
        balance += collections + other_revenue - out + draw

        rows.append({
            "Date": d,
            "Collections": collections,
            "Other Revenue": other_revenue,
            "Credit Draw": draw,
            "Payroll": pay,
            "CSS / Government": css_out,
            "Pluxee": pluxee_out,
            "Viatico": viatico_out,
            "Decimo": decimo_out,
            "Provisions": provision,
            "Severance": sev_out,
            "Other Fixed": fixed_out,
            "Interest": interest,
            "Net": collections + other_revenue + draw - out,
            "Balance": balance,
            "Credit Balance": loc_balance,
            "Credit Available": max(loc_limit - loc_balance, 0.0),
        })
    return pd.DataFrame(rows)


def monthly_summary(df):
    """Roll the daily schedule up by calendar month."""
    out = df.copy()
    out["Month"] = pd.to_datetime(out["Date"]).dt.strftime("%Y-%m")
    cols = ["Collections", "Other Revenue", "Credit Draw", "Payroll", "CSS / Government", "Pluxee",
            "Viatico", "Decimo", "Provisions", "Severance", "Other Fixed",
            "Interest", "Net"]
    g = out.groupby("Month", as_index=False)[cols].sum()
    g["Ending Balance"] = out.groupby("Month")["Balance"].last().values
    g["Starting Balance"] = g["Ending Balance"] - g["Net"]
    g["Credit Balance"] = out.groupby("Month")["Credit Balance"].last().values
    g["Days Counted"] = out.groupby("Month")["Date"].size().values
    lead = ["Month", "Starting Balance"]
    return g[lead + [c for c in g.columns if c not in lead]]


def money_table(df):
    fmt = {c: MONEY for c in df.columns if df[c].dtype.kind in "fi" and c != "Headcount"}
    st.dataframe(df.style.format(fmt), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Sage 50 imports: 12-month income statement and general ledger detail.
# ---------------------------------------------------------------------------

SECTION_HEADERS = {"Revenues", "Cost of Sales", "Expenses"}
TOTAL_LABELS = {"Total Revenues", "Total Cost of Sales", "Total Expenses",
                "Gross Profit", "Net Income"}


def _read_any(file, name):
    if str(name).lower().endswith(".csv"):
        return pd.read_csv(file, header=None)
    return pd.read_excel(file, header=None)


def read_income_statement(file, name):
    """Parse a Sage 50 12-period income statement into tidy rows.

    Returns (lines, totals) where lines has Section / Line / Period 1..12 and
    totals has one row per total or subtotal line.
    """
    raw = _read_any(file, name)

    header = raw.iloc[0].tolist()
    periods = [str(h) for h in header[1:] if isinstance(h, str) and h.strip()]
    ncols = len(periods)

    lines, totals, section = [], [], "Revenues"

    for _, row in raw.iloc[1:].iterrows():
        label = row[0]
        if not isinstance(label, str) or not label.strip():
            continue
        label = label.strip()
        values = pd.to_numeric(row[1:1 + ncols], errors="coerce").fillna(0.0)

        if label in SECTION_HEADERS and values.abs().sum() == 0:
            section = label
            continue

        record = {"Section": section, "Line": label}
        record.update({p: float(v) for p, v in zip(periods, values)})

        if label in TOTAL_LABELS or label.startswith("Total "):
            totals.append(record)
        else:
            record["Total"] = float(values.sum())
            if record["Total"] != 0:
                lines.append(record)

    return pd.DataFrame(lines), pd.DataFrame(totals), periods


def income_summary(totals, periods):
    """Pull the headline figures out of the totals table."""
    out = {}
    for key in ["Total Revenues", "Total Cost of Sales", "Total Expenses",
                "Gross Profit", "Net Income"]:
        hit = totals[totals["Line"] == key]
        if hit.empty:
            out[key] = 0.0
        else:
            out[key] = float(hit.iloc[0][periods].sum())
    return out


def monthly_series(totals, periods):
    """One row per period with revenue, cost of sales, expenses, net income."""
    def grab(key):
        hit = totals[totals["Line"] == key]
        if hit.empty:
            return [0.0] * len(periods)
        return [float(hit.iloc[0][p]) for p in periods]

    return pd.DataFrame({
        "Period": periods,
        "Revenue": grab("Total Revenues"),
        "Cost of Sales": grab("Total Cost of Sales"),
        "Expenses": grab("Total Expenses"),
        "Net Income": grab("Net Income"),
    })


def read_general_ledger(file, name):
    """Parse a Sage 50 general ledger export into one row per transaction."""
    raw = _read_any(file, name)

    account_id = None
    account_desc = None
    rows = []

    for _, row in raw.iloc[2:].iterrows():
        if pd.notna(row[0]) and str(row[0]).strip() not in ("", "Account ID"):
            account_id = str(row[0]).strip()
        if pd.notna(row[1]) and str(row[1]).strip() not in ("", "Account Description"):
            account_desc = str(row[1]).strip()

        desc = row[5] if len(row) > 5 else None
        if not isinstance(desc, str):
            continue
        desc = desc.strip()
        if desc in ("Beginning Balance", "Current Period Change", "Trans Description"):
            continue

        stamp = pd.to_datetime(row[2], errors="coerce")
        if pd.isna(stamp):
            continue

        debit = pd.to_numeric(row[6], errors="coerce")
        credit = pd.to_numeric(row[7], errors="coerce")

        rows.append({
            "Account": account_id or "",
            "Account Name": account_desc or "",
            "Date": stamp.date(),
            "Month": stamp.strftime("%Y-%m"),
            "Reference": "" if pd.isna(row[3]) else str(row[3]),
            "Journal": "" if pd.isna(row[4]) else str(row[4]),
            "Description": desc,
            "Debit": 0.0 if pd.isna(debit) else float(debit),
            "Credit": 0.0 if pd.isna(credit) else float(credit),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["Net"] = df["Debit"] - df["Credit"]
    return df


def gl_by_account(df):
    g = df.groupby(["Account", "Account Name"], as_index=False).agg(
        Entries=("Net", "size"), Debit=("Debit", "sum"),
        Credit=("Credit", "sum"), Net=("Net", "sum"))
    return g.sort_values("Net", key=abs, ascending=False)


def gl_by_month(df):
    g = df.groupby("Month", as_index=False).agg(
        Entries=("Net", "size"), Debit=("Debit", "sum"),
        Credit=("Credit", "sum"), Net=("Net", "sum"))
    return g.sort_values("Month")


# ---------------------------------------------------------------------------
# Daily Log: what actually happened. A ledger of real transactions plus the
# real bank balance typed off online banking. This never changes or replaces
# the forecast. It sits beside it so the variance is visible.
# ---------------------------------------------------------------------------

LEDGER_COLUMNS = ["Date", "Description", "Category", "Money in", "Money out"]
BANK_COLUMNS = ["Date", "Bank balance", "Note"]

# The forecast columns an actual entry can be compared against. Anything that
# is really a fixed expense line rolls up into Other Fixed, which is how the
# forecast carries it.
LEDGER_CATEGORIES = [
    "Collections", "Other Revenue", "Credit Draw",
    "Payroll", "CSS / Government", "Pluxee", "Viatico", "Decimo",
    "Severance", "Other Fixed", "Interest", "Credit Payment", "Other",
]

MONEY_IN_CATEGORIES = {"Collections", "Other Revenue", "Credit Draw"}


def category_choices():
    """Forecast categories, plus each named line from the expense schedule."""
    lines = []
    try:
        frame = load_expense_schedule()
        if not frame.empty:
            lines = [str(x).strip() for x in frame["Expense"].tolist()
                     if str(x).strip()]
    except Exception:
        lines = []
    seen, out = set(), []
    for name in LEDGER_CATEGORIES + lines:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def forecast_bucket(category):
    """Map a ledger category onto the column the forecast reports it under."""
    if category in LEDGER_CATEGORIES:
        return category
    return "Other Fixed"


def load_ledger():
    rows = kv_get("actuals_ledger")
    if isinstance(rows, list) and rows:
        frame = pd.DataFrame(rows)
        for c in LEDGER_COLUMNS:
            if c not in frame.columns:
                frame[c] = "" if c in ("Description", "Category") else 0.0
        return frame[LEDGER_COLUMNS]
    return pd.DataFrame(columns=LEDGER_COLUMNS)


def save_ledger(frame):
    frame = frame.copy()
    frame = frame.dropna(subset=["Date"])
    frame["Date"] = frame["Date"].astype(str).str.slice(0, 10)
    frame = frame[frame["Date"].str.strip() != ""]
    for c in ("Money in", "Money out"):
        frame[c] = pd.to_numeric(frame[c], errors="coerce").fillna(0.0)
    frame = frame.sort_values("Date").reset_index(drop=True)
    kv_put("actuals_ledger", frame[LEDGER_COLUMNS].to_dict("records"))
    return frame


def load_bank():
    rows = kv_get("bank_balances")
    if isinstance(rows, list) and rows:
        frame = pd.DataFrame(rows)
        for c in BANK_COLUMNS:
            if c not in frame.columns:
                frame[c] = "" if c == "Note" else 0.0
        return frame[BANK_COLUMNS]
    return pd.DataFrame(columns=BANK_COLUMNS)


def save_bank(frame):
    frame = frame.copy()
    frame = frame.dropna(subset=["Date"])
    frame["Date"] = frame["Date"].astype(str).str.slice(0, 10)
    frame = frame[frame["Date"].str.strip() != ""]
    frame["Bank balance"] = pd.to_numeric(
        frame["Bank balance"], errors="coerce").fillna(0.0)
    frame = frame.drop_duplicates(subset=["Date"], keep="last")
    frame = frame.sort_values("Date").reset_index(drop=True)
    kv_put("bank_balances", frame[BANK_COLUMNS].to_dict("records"))
    return frame


def ledger_running(frame, opening):
    """Day by day: money in, money out, and the balance the ledger implies."""
    if frame.empty:
        return pd.DataFrame(columns=["Date", "Money in", "Money out", "Net",
                                     "Balance"])
    g = frame.copy()
    g["Money in"] = pd.to_numeric(g["Money in"], errors="coerce").fillna(0.0)
    g["Money out"] = pd.to_numeric(g["Money out"], errors="coerce").fillna(0.0)
    g = g.groupby("Date", as_index=False)[["Money in", "Money out"]].sum()
    g = g.sort_values("Date").reset_index(drop=True)
    g["Net"] = g["Money in"] - g["Money out"]
    g["Balance"] = float(opening) + g["Net"].cumsum()
    return g


def ledger_by_month(frame):
    """Actuals rolled up by month and forecast bucket."""
    if frame.empty:
        return pd.DataFrame(columns=["Month", "Category", "Actual"])
    g = frame.copy()
    g["Month"] = g["Date"].astype(str).str.slice(0, 7)
    g["Category"] = g["Category"].astype(str).map(forecast_bucket)
    g["Money in"] = pd.to_numeric(g["Money in"], errors="coerce").fillna(0.0)
    g["Money out"] = pd.to_numeric(g["Money out"], errors="coerce").fillna(0.0)
    g["Actual"] = g.apply(
        lambda r: r["Money in"] if r["Category"] in MONEY_IN_CATEGORIES
        else r["Money out"], axis=1)
    out = g.groupby(["Month", "Category"], as_index=False)["Actual"].sum()
    return out


def variance_table(actual_month, forecast_monthly, month):
    """Forecast beside actual for one month, with the gap between them."""
    fc = forecast_monthly[forecast_monthly["Month"] == month]
    rows = []
    for name in LEDGER_CATEGORIES:
        if name in ("Other", "Credit Payment"):
            continue
        planned = float(fc[name].iloc[0]) if (not fc.empty
                                             and name in fc.columns) else 0.0
        got = actual_month[actual_month["Category"] == name]["Actual"]
        real = float(got.iloc[0]) if not got.empty else 0.0
        if not planned and not real:
            continue
        rows.append({"Category": name, "Forecast": planned, "Actual": real,
                     "Difference": real - planned})
    extra = actual_month[~actual_month["Category"].isin(LEDGER_CATEGORIES)]
    for _, r in extra.iterrows():
        rows.append({"Category": str(r["Category"]), "Forecast": 0.0,
                     "Actual": float(r["Actual"]),
                     "Difference": float(r["Actual"])})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Accounts and access levels
#
# Three levels. Admin owns the model and the accounts. User records facts:
# daily log, payroll, terminations, Sage actuals. Viewer reads and nothing
# more. Passwords are stored as a PBKDF2 hash with a per-user salt, never in
# plain text and never in the repository.
# --------------------------------------------------------------------------
import hashlib
import hmac
import secrets as _secrets

ROLES = ["admin", "user", "viewer"]
MAX_USERS = 3
ROLE_HELP = {
    "admin": "Everything, including forecast assumptions and accounts.",
    "user": "Can record actuals, payroll, terminations and Sage figures. "
            "Cannot change forecast assumptions or accounts.",
    "viewer": "Can read every page. Cannot change anything.",
}
PBKDF_ROUNDS = 200000


def hash_password(password, salt=None):
    salt = salt or _secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"),
                             bytes.fromhex(salt), PBKDF_ROUNDS)
    return salt + "$" + dk.hex()


def verify_password(password, stored):
    try:
        salt, want = str(stored).split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"),
                                 bytes.fromhex(salt), PBKDF_ROUNDS)
    except Exception:
        return False
    return hmac.compare_digest(dk.hex(), want)


def load_users():
    rows = kv_get("users")
    return rows if isinstance(rows, list) else []


def save_users(rows):
    kv_put("users", rows)


def find_user(rows, login):
    """Match on login or email, case-insensitively, so either one works."""
    key = str(login or "").strip().lower()
    if not key:
        return None
    for r in rows:
        if str(r.get("login", "")).strip().lower() == key:
            return r
        if str(r.get("email", "")).strip().lower() == key:
            return r
    return None


def current_user():
    return st.session_state.get("auth_user")


def role_of():
    u = current_user()
    return str(u.get("role", "viewer")) if u else "viewer"


def require_login():
    """Show the sign-in screen and stop unless someone is signed in."""
    if current_user():
        return
    users = load_users()
    st.title("7MS Forecasting Tool")
    if not users:
        st.error(
            "No accounts exist yet, so nobody can sign in. The administrator "
            "account has to be created directly in the database before this "
            "page will let anyone through."
        )
        st.stop()
    st.subheader("Sign in")
    with st.form("sign_in"):
        who = st.text_input("Username or email")
        pw = st.text_input("Password", type="password")
        go = st.form_submit_button("Sign in")
    if go:
        found = find_user(users, who)
        # Verify even when the name is unknown, so a wrong username and a
        # wrong password take the same time to answer.
        stored = found.get("password", "") if found else hash_password("x")
        if found and verify_password(pw, stored):
            st.session_state["auth_user"] = {
                "login": found.get("login", ""),
                "name": found.get("name", found.get("login", "")),
                "email": found.get("email", ""),
                "role": found.get("role", "viewer"),
                "must_change": bool(found.get("must_change", False)),
            }
            st.rerun()
        else:
            st.error("That username or password is not right.")
    st.stop()


def force_password_change():
    """A temporary password has to be replaced before anything else happens."""
    u = current_user()
    if not u or not u.get("must_change"):
        return
    st.title("Choose your password")
    st.warning(
        "You are signed in with a temporary password. Set your own before "
        "going any further. Nobody else can see what you choose, including me."
    )
    with st.form("first_password"):
        a = st.text_input("New password", type="password")
        b = st.text_input("New password again", type="password")
        go = st.form_submit_button("Save my password")
    if go:
        if len(a) < 10:
            st.error("Use at least 10 characters.")
        elif a != b:
            st.error("Those two do not match.")
        else:
            rows = load_users()
            for r in rows:
                if str(r.get("login", "")).lower() == str(u["login"]).lower():
                    r["password"] = hash_password(a)
                    r["must_change"] = False
            save_users(rows)
            st.session_state["auth_user"]["must_change"] = False
            st.success("Saved. Opening the app.")
            st.rerun()
    st.stop()


def read_only_note(what="this page"):
    st.info(
        "You are signed in as " + role_of() + ", so " + what + " is read-only. "
        "You can see every figure but not change anything. Ask Frank if "
        "something needs editing."
    )


require_login()
force_password_change()

USER = current_user()
ROLE = role_of()
# Two permissions, so every gate below reads plainly. can_enter is about
# recording what happened. can_configure is about the assumptions the
# forecast is built on, plus the accounts themselves.
can_enter = ROLE in ("admin", "user")
can_configure = ROLE == "admin"

st.sidebar.title("7MS Forecasting Tool")
PAGES = ["Dashboard", "Forecast", "Payroll", "Terminations", "Sage Actuals",
         "Cash Flow", "Daily Log", "AI Assistant"]
if can_configure:
    PAGES.append("Accounts")
page = st.sidebar.radio("Go to", PAGES)

storage_note()

st.sidebar.divider()
st.sidebar.caption("Signed in as " + str(USER.get("name") or USER.get("login")))
st.sidebar.caption("Access level: " + ROLE + ". " + ROLE_HELP.get(ROLE, ""))
with st.sidebar.expander("Change my password"):
    with st.form("own_password"):
        cur_pw = st.text_input("Current password", type="password")
        new_a = st.text_input("New password", type="password")
        new_b = st.text_input("New password again", type="password")
        changed = st.form_submit_button("Update")
    if changed:
        rows = load_users()
        me = find_user(rows, USER.get("login"))
        if not me or not verify_password(cur_pw, me.get("password", "")):
            st.error("Current password is not right.")
        elif len(new_a) < 10:
            st.error("Use at least 10 characters.")
        elif new_a != new_b:
            st.error("Those two do not match.")
        else:
            me["password"] = hash_password(new_a)
            me["must_change"] = False
            save_users(rows)
            st.success("Password updated.")
if st.sidebar.button("Sign out"):
    st.session_state.pop("auth_user", None)
    st.rerun()

st.title(page)

if page == "Dashboard":
    st.write(
        "Where the money stands today, what needs attention, and what is "
        "landing next. Everything here is read-only. Edit on the page it "
        "belongs to."
    )
    saved = load_settings()
    sev = scheduled_payments(load_terminations())
    try:
        target = date.fromisoformat(str(saved.get("end_date"))[:10])
        days = max((target - date.today()).days + 1, 30)
    except Exception:
        days = int(saved.get("horizon", 120))
    if saved.get("revenue_mode") == "Build from agent hours":
        dash_rev = agent_revenue_schedule(load_agent_schedule(),
                                          int(saved.get("revenue_day", 20)),
                                          int(saved.get("revenue_lag", 0)))
    else:
        dash_rev = load_revenue_schedule()
    df = build_schedule(saved, days, sev, load_expense_schedule(), dash_rev,
                        load_extra_revenue())

    ledger = load_ledger()
    bank = load_bank()
    bank_balance, bank_date = None, None
    if not bank.empty:
        latest = bank.sort_values("Date").iloc[-1]
        bank_balance = float(latest["Bank balance"])
        bank_date = str(latest["Date"])[:10]
    assumed = float(saved.get("start_cash", 0.0))

    # ---- what needs attention -------------------------------------------
    alerts = []
    negative = df[df["Balance"] < 0]
    if not negative.empty:
        first_neg = negative.iloc[0]
        alerts.append(
            "Cash goes negative on " + str(first_neg["Date"])[:10] + " at "
            + MONEY.format(first_neg["Balance"]) + ". "
            + str(len(negative)) + " day(s) below zero in this projection."
        )
    limit = float(saved.get("loc_limit", 0.0))
    if limit > 0:
        peak_drawn = float(df["Credit Balance"].max())
        if peak_drawn >= limit - 0.01:
            when = df[df["Credit Balance"] >= limit - 0.01].iloc[0]["Date"]
            alerts.append(
                "The line of credit is fully drawn by " + str(when)[:10]
                + ", all " + MONEY.format(limit) + " of it. There is no "
                "headroom left after that date."
            )
    sev_due = float(df["Severance"].sum())
    if sev_due:
        alerts.append("Severance due in this window: " + MONEY.format(sev_due)
                      + ".")
    if bank_balance is not None:
        drift = bank_balance - assumed
        if abs(drift) > 1000:
            alerts.append(
                "The forecast starts from " + MONEY.format(assumed)
                + " but the bank held " + MONEY.format(bank_balance) + " on "
                + bank_date + ", a gap of " + MONEY.format(drift)
                + ". Some of that gap is money the forecast still expects to "
                "arrive, so check before changing the starting balance on the "
                "Cash Flow page."
            )
    if bank_balance is None:
        alerts.append(
            "No bank balance has been entered on the Daily Log page, so there "
            "is nothing to check the forecast against."
        )

    if alerts:
        st.subheader("Needs attention")
        for line in alerts:
            st.warning(line)
    else:
        st.success("Nothing needs attention in this projection window.")

    st.divider()

    # ---- cash position ---------------------------------------------------
    st.subheader("Cash position")
    c1, c2, c3, c4 = st.columns(4)
    if bank_balance is None:
        c1.metric("Bank balance", "not entered",
                  help="Enter one on the Daily Log page.")
    else:
        c1.metric("Bank balance", MONEY.format(bank_balance),
                  help="Most recent balance typed on the Daily Log page, from "
                       + bank_date)
    c2.metric("Forecast starts from", MONEY.format(assumed),
              help="The starting balance set on the Cash Flow page. The whole "
                   "projection is built on this number.")
    c3.metric("Balance in 30 days",
              MONEY.format(df["Balance"].iloc[min(29, days - 1)]))
    c4.metric(f"Balance in {days} days", MONEY.format(df["Balance"].iloc[-1]))
    if bank_balance is not None:
        st.caption(
            "Bank balance is as of " + bank_date + ". The forecast is built "
            "from " + MONEY.format(assumed) + ", not from the bank figure, so "
            "the two are deliberately independent."
        )

    # ---- the tightest day ------------------------------------------------
    st.subheader("Lowest point")
    low_idx = df["Balance"].idxmin()
    low = df.loc[low_idx]
    l1, l2, l3 = st.columns(3)
    l1.metric("Lowest balance", MONEY.format(low["Balance"]))
    l2.metric("On", str(low["Date"])[:10])
    shortfall = max(-float(low["Balance"]), 0.0)
    l3.metric("Credit needed to cover it", MONEY.format(shortfall),
              help="How much more cash you would need on that day to stay "
                   "above zero. Zero means you never run out.")
    if limit > 0:
        st.caption(
            "Credit drawn at the lowest point: "
            + MONEY.format(float(low["Credit Balance"])) + " of "
            + MONEY.format(limit) + ". Peak draw across the window: "
            + MONEY.format(float(df["Credit Balance"].max())) + "."
        )

    # ---- tight days, and what you could delay ----------------------------
    tight = df[df["Balance"] < float(saved.get("loc_min_cash", 0.0))]
    if not tight.empty:
        st.divider()
        st.subheader("Tight days, and what you could delay")
        exp_frame = load_expense_schedule()
        first_tight = pd.to_datetime(tight.iloc[0]["Date"]).date()
        last_tight = pd.to_datetime(tight.iloc[-1]["Date"]).date()
        st.caption(
            str(len(tight)) + " day(s) fall below your minimum cash of "
            + MONEY.format(float(saved.get("loc_min_cash", 0.0)))
            + ", from " + first_tight.isoformat() + " to "
            + last_tight.isoformat() + "."
        )
        # Look at the fortnight running up to the squeeze, since a bill paid
        # just before it is the one worth holding back.
        look_from = first_tight - timedelta(days=14)
        flex = flexible_between(exp_frame, look_from, last_tight)
        if flex.empty:
            st.info(
                "No lines are marked flexible in that window. Tick Flexible on "
                "the Cash Flow page for any bill you could pay late, typically "
                "net 30 terms, and it will show up here."
            )
        else:
            money_table(flex)
            st.metric("Flexible in that window",
                      MONEY.format(float(flex["Amount"].sum())))
            worst = float(tight["Balance"].min())
            need = max(-worst, 0.0) if worst < 0 else 0.0
            if need and float(flex["Amount"].sum()) >= need:
                st.success(
                    "Delaying " + MONEY.format(need) + " of those bills past "
                    "the squeeze would keep you above zero. There is "
                    + MONEY.format(float(flex["Amount"].sum()))
                    + " of flexible spend in the window, so it is coverable."
                )
            elif need:
                st.warning(
                    "The shortfall is " + MONEY.format(need)
                    + " but only " + MONEY.format(float(flex["Amount"].sum()))
                    + " in that window is flexible. Delaying alone will not "
                    "close it."
                )
            st.caption(
                "Nothing has been moved. This is only what is available to "
                "move, and which days it currently sits on."
            )

    st.divider()

    # ---- the next fortnight ----------------------------------------------
    st.subheader("Next 14 days")
    window = df[pd.to_datetime(df["Date"]) <=
                pd.Timestamp(date.today() + timedelta(days=13))]
    OUT_COLS = ["Payroll", "CSS / Government", "Pluxee", "Viatico", "Decimo",
                "Severance", "Other Fixed", "Interest"]
    IN_COLS = ["Collections", "Other Revenue", "Credit Draw"]
    events = []
    for _, r in window.iterrows():
        for c in IN_COLS + OUT_COLS:
            amount = float(r.get(c, 0.0) or 0.0)
            if abs(amount) < 0.01:
                continue
            events.append({
                "Date": str(r["Date"])[:10],
                "What": c,
                "In": amount if c in IN_COLS else 0.0,
                "Out": amount if c in OUT_COLS else 0.0,
                "Balance after": float(r["Balance"]),
            })
    if not events:
        st.info("Nothing scheduled in the next 14 days.")
    else:
        ev = pd.DataFrame(events)
        money_table(ev)
        t1, t2, t3 = st.columns(3)
        t1.metric("Coming in", MONEY.format(ev["In"].sum()))
        t2.metric("Going out", MONEY.format(ev["Out"].sum()))
        t3.metric("Net", MONEY.format(ev["In"].sum() - ev["Out"].sum()))
        st.caption(
            "Straight from the forecast, not from the log, so it is what is "
            "scheduled rather than what has happened."
        )

    st.divider()

    # ---- this month, plan against reality --------------------------------
    st.subheader("This month: forecast against actual")
    this_month = date.today().strftime("%Y-%m")
    actual_monthly = ledger_by_month(ledger)
    plan_monthly = monthly_summary(df)
    mine = actual_monthly[actual_monthly["Month"] == this_month] \
        if not actual_monthly.empty else actual_monthly
    if actual_monthly.empty or mine.empty:
        st.info(
            "Nothing logged for " + this_month + " yet. Add entries on the "
            "Daily Log page and the comparison appears here."
        )
    else:
        table = variance_table(mine, plan_monthly, this_month)
        if table.empty:
            st.info("Nothing to compare for " + this_month + " yet.")
        else:
            money_table(table)
        covered = plan_monthly[plan_monthly["Month"] == this_month]
        counted = int(covered["Days Counted"].iloc[0]) if not covered.empty else 0
        month_len = calendar.monthrange(date.today().year,
                                        date.today().month)[1]
        if 0 < counted < month_len:
            st.warning(
                "The forecast only runs forward from today, so its column "
                "covers " + str(counted) + " of " + str(month_len)
                + " days this month while the log may cover the whole month. "
                "Anything paid before today shows an actual with no forecast "
                "beside it. Full months compare cleanly."
            )

    st.divider()

    # ---- payroll and the year -------------------------------------------
    st.subheader("Payroll and provisions")
    p1, p2, p3 = st.columns(3)
    p1.metric("Monthly payroll", MONEY.format(saved.get("payroll", 0.0)))
    p2.metric("Monthly CSS / government", MONEY.format(saved.get("css", 0.0)))
    p3.metric("Decimo per payment", MONEY.format(saved.get("decimo", 0.0)))
    st.caption(
        "Decimo is paid 15 April, 15 August and 15 December. Payroll splits "
        "half on the 15th and half on the last day of the month. Figures come "
        "from the Payroll page, whichever basis you last sent."
    )

    st.subheader("Projected balance")
    st.line_chart(df.set_index("Date")["Balance"])
    with st.expander("Month by month"):
        money_table(plan_monthly)


elif page == "Forecast":
    st.write("Monthly forecasting by employee group.")
    revenue = st.number_input("Current monthly revenue ($)", min_value=0.0, step=100.0)
    growth = st.number_input("Expected monthly growth rate (%)", value=0.0, step=0.1)
    months = st.slider("Months to forecast", 1, 24, 12)
    projected = revenue * (1 + growth / 100) ** months
    st.metric(f"Estimated revenue in {months} months", MONEY.format(projected))

elif page == "Payroll":
    st.write(
        "Upload a DV Pre-Planilla export (Excel or CSV). Salary is paid on the 15th and "
        "the final day of the month; CSS and government amounts are due at month end in arrears."
    )

    stored = doc_list("payroll")
    df, meta = None, {}

    if stored:
        names = [r["name"] for r in stored]
        choice = st.selectbox("Saved payroll periods", ["Upload a new file"] + names)
    else:
        choice = "Upload a new file"
        st.caption("No periods saved yet. Upload a file and you can save it below.")

    if choice == "Upload a new file":
        upload = st.file_uploader("DV Pre-Planilla file", type=["xlsx", "xls", "csv"])
        if upload is None:
            st.info(
                "Choose a file to see headcount, pay, and employer cost by employee "
                "group. Once a period is saved you can reopen it without uploading."
            )
            st.stop()
        try:
            df, meta = read_payroll(upload, upload.name)
        except Exception as exc:
            st.error(f"Could not read that file: {exc}")
            st.stop()
        st.session_state["payroll_new"] = True
    else:
        df = doc_get("payroll", choice)
        if df is None:
            st.error("That saved period could not be read.")
            st.stop()
        row = next((r for r in stored if r["name"] == choice), {})
        try:
            meta = json.loads(row.get("notes") or "{}")
        except Exception:
            meta = {}
        st.success(f"Loaded saved period: {choice}")
        st.session_state["payroll_new"] = False

    if meta.get("Detalle"):
        st.caption(f"Period: {meta['Detalle']}")
    if meta.get("CompaNia"):
        st.caption(f"Company: {meta['CompaNia']}  ·  Planilla: {meta.get('Planilla', '')}")

    s = summarize(df)

    a, b, c, d = st.columns(4)
    a.metric("Active employees", f"{s['headcount']:,}")
    b.metric("Gross pay", MONEY.format(s["gross"]))
    c.metric("Net pay (cash to staff)", MONEY.format(s["net"]))
    d.metric("Employer cost", MONEY.format(s["employer_cost"]))

    e, f, g, h = st.columns(4)
    e.metric("Total deductions", MONEY.format(s["deductions"]))
    f.metric("Employee statutory withheld", MONEY.format(s["employee_statutory"]))
    g.metric("Overtime", MONEY.format(s["overtime"]))
    h.metric("Viatico (non-taxable)", MONEY.format(s["viatico"]))

    k, m, n, o = st.columns(4)
    k.metric("Decimo paid (cash)", MONEY.format(s["decimo_paid"]))
    m.metric("Decimo accrued (not cash)", MONEY.format(s["decimo_accrued"]))
    n.metric("Vacation paid", MONEY.format(s["vacation"]))
    o.metric("Pass-through loans", MONEY.format(s["loans"]))

    if s["decimo_paid"] > 0:
        st.warning(
            "This period includes decimo (13th month) of "
            f"{MONEY.format(s['decimo_paid'])}. Decimo is paid on 15 April, "
            "15 August, and 15 December, so it is excluded from the recurring "
            "payroll figure below and tracked as its own cash line."
        )

    st.subheader("By employee group")
    groups = by_group(df)
    money_table(groups)

    st.subheader("Loan and deduction programs")
    st.caption(
        "These come out of employee pay and are not company operating expenses."
    )
    loans = loan_detail(df)
    if loans.empty:
        st.write("No loan deductions in this period.")
    else:
        money_table(loans)
        st.metric("Total pass-through deductions", MONEY.format(s["loans"]))

    with st.expander("Employee detail"):
        cols = [c for c in ["NOMBRE", "CARGO", "GRUPO", "SALARIO_MENSUAL",
                            "INGRESO_BRUTO", "TOTAL_RETENCIONES", "INGRESO_NETO",
                            "TOTAL_GASTO_PAT", "TIPO_PAGO"] if c in df.columns]
        st.dataframe(df[cols], use_container_width=True, hide_index=True)

    st.download_button(
        "Download group summary (CSV)",
        data=groups.to_csv(index=False),
        file_name="payroll-by-group.csv",
        mime="text/csv",
    )

    st.divider()
    st.subheader("Send to Cash Flow")
    period_label, detected = payroll.period_kind(meta)
    st.caption(
        "This file covers one pay period. To get monthly figures the amounts "
        "are multiplied by how many times payroll runs in a month. Read from "
        f"the file: {period_label}."
    )
    period_choices = {
        "Quincenal - twice a month": 2.0,
        "Mensual - once a month": 1.0,
        "Bisemanal - every two weeks": 2.1667,
        "Semanal - weekly": 4.3333,
    }
    names = list(period_choices)
    default_name = next((n for n in names if period_choices[n] == detected),
                        names[0])
    picked = st.selectbox(
        "How often payroll runs", names, index=names.index(default_name),
        help="If this file already covers a whole month, choose mensual so the "
             "figures are not doubled.")
    per_month = period_choices[picked]
    tot_early = payroll.accrual_totals(df)
    # A period that lands on 15 April, August or December carries the decimo
    # payment inside net pay, and a period with terminations carries the
    # liquidacion. Doubling either one invents cash that will never go out, so
    # both are stripped out here and tracked on their own lines instead.
    one_off = tot_early["decimo_cash"] + tot_early["liquidacion_cash"]
    third = payroll.third_party_deductions(df)
    include_third = st.checkbox(
        "Include third party deductions in payroll cash", value=True,
        help="These are the employee's own debts, funded out of their pay, but "
             "the company writes the cheque to the cooperative or finance "
             "company. Ticked, the cash leaves the bank. Unticked, it is "
             "treated as the employee's affair and left out entirely.")
    third_cash = third["remitted"] if include_third else 0.0
    monthly_payroll = (s["net"] - one_off + third_cash) * per_month
    monthly_css = (s["employer_cost"] + s["employee_statutory"]
                   - tot_early["isr_on_liquidacion"]) * per_month
    monthly_viatico = s["viatico"] * per_month
    # Decimo is one month of pay a year paid in three installments, so each
    # installment covers four months of accrual.
    decimo_payment = s["decimo_accrued"] * per_month * 4

    st.caption(
        f"Multiplier in use: {per_month:g}. Net pay in this file is "
        + MONEY.format(s["net"]) + ". Take out one-off decimo and liquidacion "
        "payouts and it is " + MONEY.format(s["net"] - one_off)
        + (", then add " + MONEY.format(third_cash)
           + " withheld for third parties" if include_third else
           ", with third party deductions left out")
        + ", so monthly payroll cash is "
        + MONEY.format(monthly_payroll) + "."
    )
    with st.expander("Third party deductions inside this payroll run"):
        st.caption(
            "These are the employee's own obligations, deducted from their pay. "
            "The company is only the middleman, but the cash still crosses the "
            "bank account on its way to the cooperative or finance company, so "
            "counting it keeps the projected balance honest. Employee "
            "receivables are excluded, since that money comes back to you."
        )
        if third["detail"].empty:
            st.info("Nothing withheld for third parties in this period.")
        else:
            money_table(third["detail"])
        st.caption(
            "Per period " + MONEY.format(third["remitted"]) + ", or "
            + MONEY.format(third["remitted"] * per_month) + " a month. "
            "Employee receivables coming back in: "
            + MONEY.format(third["receivable"]) + " per period."
        )
    if one_off > 0:
        st.warning(
            "This period includes "
            + MONEY.format(tot_early["decimo_cash"]) + " of decimo paid and "
            + MONEY.format(tot_early["liquidacion_cash"])
            + " of liquidacion. Those are one-off payments, so they are left "
            "out of the monthly payroll figure. Decimo has its own line, and "
            "terminations belong on the Terminations page. Leaving them in and "
            "multiplying would create cash that never actually leaves."
        )


    i, j = st.columns(2)
    i.metric("Monthly payroll cash", MONEY.format(monthly_payroll))
    j.metric("Monthly CSS / government", MONEY.format(monthly_css))
    k2, l2 = st.columns(2)
    k2.metric("Monthly viatico", MONEY.format(monthly_viatico))
    l2.metric("Decimo per payment", MONEY.format(decimo_payment))
    st.caption(
        "Decimo per payment is this period's accrual carried over four months, "
        "since each of the three payments covers a third of the year. Pluxee is "
        "not part of the planilla, so it stays a manual entry on the Cash Flow page."
    )
    st.divider()
    st.subheader("What in this file is cash and what is only accrued")
    st.caption(
        "The planilla carries both. Provision columns build a liability and no "
        "money leaves the bank in the period. Benefit payouts are real cash, "
        "because somebody was actually paid vacation, a decimo advance, or a "
        "liquidacion, and those amounts already sit inside net pay."
    )
    split = payroll.accrual_breakdown(df, per_month)
    if split.empty:
        st.info("This period has no benefit or provision lines.")
    else:
        money_table(split)

    tot = payroll.accrual_totals(df)
    regular_payroll = (s["net"] - tot["benefit_cash_total"]
                       + third_cash) * per_month
    decimo_provision = tot["decimo_accrued"] * per_month
    vacation_provision = tot["vacation_accrued"] * per_month

    n1, n2, n3 = st.columns(3)
    n1.metric("Accrued this period, no cash",
              MONEY.format(tot["accrued_total"]))
    n2.metric("Benefit payouts inside net pay",
              MONEY.format(tot["benefit_cash_total"]))
    n3.metric("Regular payroll cash per month", MONEY.format(regular_payroll))
    st.caption(
        "Regular payroll cash strips the benefit payouts out of net pay, so it "
        "is the ordinary wage run on its own. It is an estimate, since the file "
        "does not split deductions line by line."
    )

    st.divider()
    st.subheader("Which basis do you want to send")
    st.caption(
        "Cash basis sends all the money that actually leaves the bank, so net "
        "pay stays whole and decimo lands as a lump on 15 April, 15 August and "
        "15 December. Accrual basis sends the ordinary wage run plus monthly "
        "decimo and vacation provisions, which is how your books carry it."
    )
    bc1, bc2 = st.columns(2)
    if bc1.button("Send cash basis numbers", disabled=not can_enter):
        saved = load_settings()
        saved["payroll"] = round(monthly_payroll, 2)
        saved["css"] = round(monthly_css, 2)
        saved["viatico"] = round(monthly_viatico, 2)
        saved["decimo"] = round(decimo_payment, 2)
        saved["decimo_provision"] = 0.0
        saved["vacation_provision"] = 0.0
        saved["basis"] = "Cash basis"
        save_settings(saved)
        st.success(
            "Cash Flow set to cash basis: payroll "
            + MONEY.format(monthly_payroll) + " per month, decimo "
            + MONEY.format(decimo_payment) + " per payment, no provisions."
        )
    if bc2.button("Send accrual basis numbers", disabled=not can_enter):
        saved = load_settings()
        saved["payroll"] = round(regular_payroll, 2)
        saved["css"] = round(monthly_css, 2)
        saved["viatico"] = round(monthly_viatico, 2)
        saved["decimo"] = round(decimo_payment, 2)
        saved["decimo_provision"] = round(decimo_provision, 2)
        saved["vacation_provision"] = round(vacation_provision, 2)
        saved["basis"] = "Accrual basis"
        save_settings(saved)
        st.success(
            "Cash Flow set to accrual basis: regular payroll "
            + MONEY.format(regular_payroll) + " per month, decimo provision "
            + MONEY.format(decimo_provision) + " and vacation provision "
            + MONEY.format(vacation_provision) + " per month."
        )
    if tot["liquidacion_cash"] > 0:
        st.info(
            "This period paid " + MONEY.format(tot["liquidacion_cash"])
            + " of liquidacion. Add those on the Terminations page so they "
            "land on the date each one is actually due."
        )

    st.divider()
    st.subheader("Save this period")
    if db_ready():
        default_name = str(meta.get("Detalle") or "period").strip()[:120]
        label = st.text_input("Name for this period", value=default_name)
        s1, s2 = st.columns(2)
        if s1.button("Save to database", disabled=not can_enter):
            if doc_put("payroll", label, df, notes=json.dumps(meta, default=str)):
                st.success(f"Saved as {label}. Pick it from the list next time.")
        if choice != "Upload a new file" and s2.button("Delete this saved period"):
            doc_delete("payroll", choice)
            st.success("Deleted. Refresh the page.")
    else:
        st.warning(
            "No database configured, so periods cannot be saved yet. Add a "
            "DATABASE_URL secret in the app settings."
        )

elif page == "Terminations":
    st.write(
        "Estimate a liquidacion and schedule it 30 days after the termination date. "
        "Prima de antiguedad (Art. 224) is owed on every indefinite contract regardless "
        "of cause; indemnizacion (Art. 225) applies only to despido injustificado."
    )

    with st.form("new_termination"):
        a, b = st.columns(2)
        with a:
            name = st.text_input("Employee name")
            group = st.text_input("Employee group", value="Agents")
            monthly = st.number_input("Monthly salary ($)", min_value=0.0, step=50.0)
            reason = st.selectbox("Reason for termination", REASONS)
        with b:
            hire = st.date_input("Hire date", value=date(2024, 1, 1),
                                 min_value=date(1990, 1, 1))
            term = st.date_input("Termination date", value=date.today(),
                                 min_value=date(1990, 1, 1))
            vac_through = st.date_input(
                "Vacation paid through",
                value=max(hire, term - timedelta(days=365)),
                min_value=date(1990, 1, 1),
                help="Vacation accrues at 30 days per 11 months worked after this date.",
            )
            vac_override = st.number_input(
                "Vacation days owed (0 = calculate for me)", min_value=0.0, step=0.5,
                value=0.0,
            )
        run = st.form_submit_button("Estimate liquidacion")

    if run:
        if term < hire:
            st.error("The termination date is before the hire date.")
            st.stop()

        result = estimate(
            monthly, hire, term, reason,
            vacation_since=vac_through,
            vacation_days=vac_override if vac_override > 0 else None,
        )

        st.subheader("Estimate")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total liquidacion", MONEY.format(result["total"]))
        c2.metric("Payment date", str(result["payment_date"]))
        c3.metric("Years of service", f"{result['years_of_service']:.2f}")

        detail = pd.DataFrame([
            {"Component": "Prima de antiguedad (Art. 224)",
             "Amount": result["prima_antiguedad"]},
            {"Component": "Indemnizacion (Art. 225)",
             "Amount": result["indemnizacion"]},
            {"Component": "Decimo proporcional",
             "Amount": result["decimo_proporcional"]},
            {"Component": f"Vacaciones proporcionales ({result['vacation_days']:.1f} days)",
             "Amount": result["vacaciones_proporcionales"]},
            {"Component": "Total", "Amount": result["total"]},
        ])
        money_table(detail)

        st.caption(
            f"Weekly salary {MONEY.format(result['weekly_salary'])} "
            f"(monthly x 12 / 52)  ·  daily salary "
            f"{MONEY.format(result['daily_salary'])} (monthly / 30)  ·  decimo accrued "
            f"since {last_decimo_date(term)}"
        )
        if result["indemnizacion"] == 0:
            st.info("No indemnizacion: Art. 225 applies only to despido injustificado.")

        st.session_state["pending_termination"] = {
            "name": name or "(no name)",
            "group": group,
            "monthly_salary": monthly,
            "hire_date": str(hire),
            "termination_date": str(term),
            "reason": reason,
            "prima_antiguedad": result["prima_antiguedad"],
            "indemnizacion": result["indemnizacion"],
            "decimo_proporcional": result["decimo_proporcional"],
            "vacaciones_proporcionales": result["vacaciones_proporcionales"],
            "total": result["total"],
            "payment_date": str(result["payment_date"]),
            "paid": False,
        }

    if st.session_state.get("pending_termination"):
        if st.button("Add to upcoming payments", disabled=not can_enter):
            add_termination(st.session_state.pop("pending_termination"))
            st.success("Added. It now appears in the Cash Flow projection.")

    st.divider()
    st.subheader("Upcoming severance payments")
    records = load_terminations()
    if not records:
        st.info("Nothing scheduled yet.")
    else:
        table = pd.DataFrame(records)
        show = [c for c in ["name", "group", "reason", "termination_date",
                            "payment_date", "total", "paid"] if c in table.columns]
        table = table[show].rename(columns={
            "name": "Employee", "group": "Group", "reason": "Reason",
            "termination_date": "Terminated", "payment_date": "Pay On",
            "total": "Amount", "paid": "Paid",
        })
        st.dataframe(table, use_container_width=True, hide_index=True)

        unpaid = sum(float(r["total"]) for r in records if not r.get("paid"))
        st.metric("Total unpaid severance", MONEY.format(unpaid))

        labels = [f"{i}: {r.get('name')} — {r.get('payment_date')} — "
                  f"{MONEY.format(float(r.get('total', 0)))}"
                  for i, r in enumerate(records)]
        pick = st.selectbox("Select a record", labels) if labels else None
        if pick:
            idx = int(pick.split(":")[0])
            m1, m2 = st.columns(2)
            if m1.button("Mark as paid"):
                records[idx]["paid"] = True
                save_terminations(records)
                st.success("Marked as paid and removed from the cash projection.")
            if m2.button("Delete record"):
                records.pop(idx)
                save_terminations(records)
                st.success("Deleted.")

        st.download_button(
            "Download severance records (JSON)",
            data=json.dumps(records, indent=2, default=str),
            file_name="terminations.json",
            mime="application/json",
        )

elif page == "Sage Actuals":
    st.write(
        "Upload your Sage 50 reports to see actual results. The 12-month income "
        "statement gives revenue, cost of sales, and net income by period. The "
        "general ledger export gives transaction-level detail behind each account."
    )

    tab_is, tab_gl = st.tabs(["Income Statement", "General Ledger"])

    with tab_is:
        choice_is, rows_is = stored_picker(
            "sage_is", "Saved income statements",
            "No income statements saved yet. Upload one and you can save it below.")

        body_is, source_name = None, ""
        if choice_is == "Upload a new file":
            up = st.file_uploader("12-month income statement",
                                  type=["xlsx", "xls", "csv"], key="is_upload")
            if up is not None:
                body_is = sheet_text(up, up.name)
                source_name = Path(up.name).stem
        else:
            body_is = raw_get("sage_is", choice_is)
            source_name = choice_is
            if body_is is not None:
                st.success(f"Loaded saved statement: {choice_is}")

        if body_is is None:
            st.info("Upload the income statement to see actuals by period.")
        else:
            try:
                lines, totals, periods = read_income_statement(
                    io.StringIO(body_is), "saved.csv")
            except Exception as exc:
                st.error(f"Could not read that file: {exc}")
                periods = []
            if not periods:
                st.warning("No periods found in that file.")
            else:

                st.subheader("Periods to average")
                st.caption(
                    "Totals and tables below always show every period. This range only "
                    "controls the monthly averages and what gets sent to Cash Flow, so "
                    "you can leave out empty months and one-off cleanup months."
                )
                r1, r2 = st.columns(2)
                first = r1.selectbox("From", periods, index=0)
                last = r2.selectbox("To", periods, index=len(periods) - 1)
                i_first, i_last = periods.index(first), periods.index(last)
                if i_first > i_last:
                    i_first, i_last = i_last, i_first
                avg_periods = periods[i_first:i_last + 1]
                st.caption(f"Averaging over {len(avg_periods)} period(s): "
                           f"{avg_periods[0]} through {avg_periods[-1]}")

                summary = income_summary(totals, periods)
                avg_summary = income_summary(totals, avg_periods)
                a, b, c, d = st.columns(4)
                a.metric("Total revenue", MONEY.format(summary["Total Revenues"]))
                b.metric("Cost of sales", MONEY.format(summary["Total Cost of Sales"]))
                c.metric("Operating expenses", MONEY.format(summary["Total Expenses"]))
                d.metric("Net income", MONEY.format(summary["Net Income"]))

                months = len(avg_periods)
                e, f, g, h = st.columns(4)
                e.metric("Gross profit", MONEY.format(summary["Gross Profit"]))
                margin = (summary["Gross Profit"] / summary["Total Revenues"] * 100
                          if summary["Total Revenues"] else 0.0)
                f.metric("Gross margin", f"{margin:.1f}%")
                g.metric("Average monthly revenue",
                         MONEY.format(avg_summary["Total Revenues"] / months if months else 0))
                h.metric("Average monthly net income",
                         MONEY.format(avg_summary["Net Income"] / months if months else 0))
                st.caption(
                    "The first two figures cover the full statement. The two averages "
                    f"cover {avg_periods[0]} through {avg_periods[-1]} only."
                )

                if summary["Net Income"] < 0:
                    st.error(
                        "Net loss for the period of "
                        + MONEY.format(abs(summary["Net Income"]))
                    )
                else:
                    st.success("Net profit for the period.")

                series = monthly_series(totals, periods)

                st.subheader("Revenue vs cost by period")
                st.bar_chart(series.set_index("Period")[["Revenue", "Cost of Sales",
                                                         "Expenses"]])

                st.subheader("Net income by period")
                st.bar_chart(series.set_index("Period")["Net Income"])

                st.subheader("Totals by period")
                money_table(series)

                st.subheader("Line detail")
                section = st.radio("Section", ["Cost of Sales", "Expenses", "Revenues"],
                                   horizontal=True)
                part = lines[lines["Section"] == section].copy()
                part = part[["Line", "Total"] + periods].sort_values("Total",
                                                                     ascending=False)
                money_table(part)

                biggest = part.head(10)[["Line", "Total"]].set_index("Line")
                st.subheader(f"Largest {section.lower()} lines")
                st.bar_chart(biggest)

                st.divider()
                st.subheader("Send to Cash Flow")
                st.caption(
                    "Non-payroll cost lines averaged over the periods you selected "
                    "above. Payroll, CSS, viatico, and decimo are already tracked "
                    "separately from the planilla, so they are left out of this figure."
                )
                payroll_words = ["salario", "salary", "xiii", "vacacion", "seguro social",
                                 "seguro educativo", "seguro educat", "riesgos",
                                 "indemniza", "prima de antiguedad", "preaviso",
                                 "viatico", "wages", "payroll tax"]
                cost = lines[lines["Section"].isin(["Cost of Sales", "Expenses"])].copy()
                cost["Selected Total"] = cost[avg_periods].sum(axis=1)
                cost["Label"] = cost["Section"] + " - " + cost["Line"]

                suggested = [
                    r["Label"] for _, r in cost.iterrows()
                    if any(w in str(r["Line"]).lower() for w in payroll_words)
                ]
                remembered = kv_get("excluded_lines")
                if isinstance(remembered, list) and remembered:
                    default_excluded = [x for x in remembered
                                        if x in set(cost["Label"])]
                else:
                    default_excluded = suggested

                st.caption(
                    "Anything already tracked from the planilla has to come out of "
                    "this figure or it is counted twice. Wage and statutory lines "
                    "are ticked for you. Add management or support compensation, "
                    "professional fees, or any other line that payroll already "
                    "covers. Your choice is remembered."
                )
                excluded = st.multiselect(
                    "Lines to leave out, because payroll already covers them",
                    options=sorted(cost["Label"]), default=default_excluded)

                left_out = cost[cost["Label"].isin(excluded)]
                other = cost[~cost["Label"].isin(excluded)]
                monthly_other = (other["Selected Total"].sum() / months
                                 if months else 0.0)
                monthly_left_out = (left_out["Selected Total"].sum() / months
                                    if months else 0.0)

                o1, o2, o3 = st.columns(3)
                o1.metric("Average monthly other fixed expenses",
                          MONEY.format(monthly_other))
                o2.metric("Left out, per month",
                          MONEY.format(monthly_left_out))
                o3.metric(f"Included total across {months} period(s)",
                          MONEY.format(other["Selected Total"].sum()))

                with st.expander("Which lines are included"):
                    money_table(other[["Section", "Line", "Selected Total", "Total"]]
                                .sort_values("Selected Total", ascending=False))
                with st.expander("Which lines were left out"):
                    if left_out.empty:
                        st.info("Nothing is being left out right now.")
                    else:
                        money_table(left_out[["Section", "Line", "Selected Total",
                                              "Total"]]
                                    .sort_values("Selected Total", ascending=False))

                st.markdown("**Adjust each line to what you expect going forward**")
                st.caption(
                    "History is not always the plan. A cost that ran at sixty "
                    "thousand a month and has since been cancelled would drag "
                    "the average up forever, so override it here. The Sage "
                    "average is shown beside your figure, and the day of month "
                    "is when that money actually leaves the bank. Your edits "
                    "are remembered."
                )
                saved_overrides = kv_get("line_overrides") or {}
                plan = pd.DataFrame({
                    "Line": other["Label"].values,
                    "Sage monthly average": (other["Selected Total"].values / months
                                             if months else other["Selected Total"].values),
                })
                plan["Use this amount"] = [
                    float(saved_overrides.get(l, {}).get("amount", a))
                    for l, a in zip(plan["Line"], plan["Sage monthly average"])
                ]
                plan["Day of month"] = [
                    int(saved_overrides.get(l, {}).get("day", 15))
                    for l in plan["Line"]
                ]
                plan = plan.sort_values("Sage monthly average", ascending=False)
                edited = st.data_editor(
                    plan, use_container_width=True, hide_index=True,
                    disabled=(True if not can_configure
                              else ["Line", "Sage monthly average"]),
                    column_config={
                        "Sage monthly average": st.column_config.NumberColumn(
                            format="$%.2f"),
                        "Use this amount": st.column_config.NumberColumn(
                            format="$%.2f", min_value=0.0),
                        "Day of month": st.column_config.NumberColumn(
                            min_value=1, max_value=31, step=1),
                    },
                    key="line_plan",
                )

                planned_total = float(pd.to_numeric(
                    edited["Use this amount"], errors="coerce").fillna(0).sum())
                average_total = float(pd.to_numeric(
                    edited["Sage monthly average"], errors="coerce").fillna(0).sum())
                p1, p2 = st.columns(2)
                p1.metric("Planned monthly total", MONEY.format(planned_total))
                p2.metric("Difference from the Sage average",
                          MONEY.format(planned_total - average_total))

                def remember_lines():
                    kv_put("excluded_lines", excluded)
                    kv_put("line_overrides", {
                        str(r["Line"]): {"amount": float(r["Use this amount"] or 0),
                                         "day": int(r["Day of month"] or 15)}
                        for _, r in edited.iterrows()
                    })

                u1, u2 = st.columns(2)
                if u1.button("Use the Sage average as my other fixed expenses",
                             disabled=not can_configure):
                    saved = load_settings()
                    saved["fixed"] = round(monthly_other, 2)
                    save_settings(saved)
                    remember_lines()
                    st.success(
                        "Cash Flow other fixed expenses set to "
                        + MONEY.format(monthly_other) + " per month, with "
                        + MONEY.format(monthly_left_out)
                        + " a month left out as already covered by payroll."
                    )
                if u2.button("Use my adjusted figures and their due days"):
                    saved = load_settings()
                    saved["fixed"] = round(planned_total, 2)
                    saved["expense_mode"] = "Use my due-date schedule"
                    save_settings(saved)
                    remember_lines()
                    rows = pd.DataFrame({
                        "Expense": edited["Line"],
                        "Monthly amount": pd.to_numeric(
                            edited["Use this amount"], errors="coerce").fillna(0.0),
                        "Day of month": pd.to_numeric(
                            edited["Day of month"], errors="coerce").fillna(15).astype(int),
                    })
                    rows = rows[rows["Monthly amount"] > 0]
                    save_expense_schedule(rows)
                    st.success(
                        "Cash Flow set to your adjusted figures, "
                        + MONEY.format(planned_total)
                        + f" a month across {len(rows)} lines, each on the day you "
                        "chose. Cash Flow timing was switched to the due-date "
                        "schedule."
                    )

                st.download_button(
                    "Download line detail (CSV)",
                    data=lines.to_csv(index=False),
                    file_name="sage-income-lines.csv",
                    mime="text/csv",
                )

                st.divider()
                st.subheader("Save this statement")
                if db_ready():
                    name_is = st.text_input("Name for this statement",
                                            value=source_name or "income statement",
                                            key="name_is")
                    b1, b2 = st.columns(2)
                    if b1.button("Save to database", key="save_is",
                                 disabled=not can_enter):
                        raw_put("sage_is", name_is, body_is,
                                notes=f"{len(periods)} periods")
                        st.success(f"Saved as {name_is}.")
                    if choice_is != "Upload a new file" and b2.button(
                            "Delete this saved statement", key="del_is"):
                        doc_delete("sage_is", choice_is)
                        st.success("Deleted. Refresh the page.")
                else:
                    st.warning("No database configured, so this cannot be saved yet.")

    with tab_gl:
        choice_gl, rows_gl = stored_picker(
            "sage_gl", "Saved general ledgers",
            "No ledgers saved yet. Upload one and you can save it below.")

        body_gl, gl_name = None, ""
        if choice_gl == "Upload a new file":
            up_gl = st.file_uploader("General ledger export",
                                     type=["xlsx", "xls", "csv"], key="gl_upload")
            if up_gl is not None:
                body_gl = sheet_text(up_gl, up_gl.name)
                gl_name = Path(up_gl.name).stem
        else:
            body_gl = raw_get("sage_gl", choice_gl)
            gl_name = choice_gl
            if body_gl is not None:
                st.success(f"Loaded saved ledger: {choice_gl}")

        if body_gl is None:
            st.info("Upload the GL export to browse transactions by account and month.")
        else:
            try:
                gl = read_general_ledger(io.StringIO(body_gl), "saved.csv")
            except Exception as exc:
                st.error(f"Could not read that file: {exc}")
                gl = pd.DataFrame()

            if gl.empty:
                st.warning("No transactions found in that file.")
            else:

                a, b, c, d = st.columns(4)
                a.metric("Transactions", f"{len(gl):,}")
                b.metric("Accounts", f"{gl['Account'].nunique():,}")
                c.metric("Total debits", MONEY.format(gl["Debit"].sum()))
                d.metric("Total credits", MONEY.format(gl["Credit"].sum()))

                gap = gl["Debit"].sum() - gl["Credit"].sum()
                if abs(gap) < 0.01:
                    st.success("Debits and credits balance.")
                else:
                    st.warning(f"Debits and credits differ by {MONEY.format(gap)}.")

                st.subheader("Activity by month")
                money_table(gl_by_month(gl))

                st.subheader("Activity by account")
                accounts = gl_by_account(gl)
                money_table(accounts.head(40))

                st.subheader("Transaction lookup")
                f1, f2 = st.columns(2)
                labels = ["All accounts"] + [
                    f"{r.Account} - {r._2}" for r in
                    accounts[["Account", "Account Name"]].itertuples()
                ]
                pick = f1.selectbox("Account", labels)
                month_pick = f2.selectbox("Month", ["All months"] +
                                          sorted(gl["Month"].unique().tolist()))
                search = st.text_input("Search the description")

                view = gl.copy()
                if pick != "All accounts":
                    view = view[view["Account"] == pick.split(" - ")[0]]
                if month_pick != "All months":
                    view = view[view["Month"] == month_pick]
                if search.strip():
                    view = view[view["Description"].str.contains(search.strip(),
                                                                 case=False, na=False)]

                st.caption(f"{len(view):,} matching transactions  ·  net "
                           + MONEY.format(view["Net"].sum()))
                st.dataframe(
                    view[["Date", "Account", "Account Name", "Reference", "Journal",
                          "Description", "Debit", "Credit"]].style.format(
                        {"Debit": MONEY, "Credit": MONEY}),
                    use_container_width=True, hide_index=True,
                )

                st.download_button(
                    "Download these transactions (CSV)",
                    data=view.to_csv(index=False),
                    file_name="sage-gl-filtered.csv",
                    mime="text/csv",
                )

                st.divider()
                st.subheader("Save this ledger")
                if db_ready():
                    name_gl = st.text_input("Name for this ledger",
                                            value=gl_name or "general ledger",
                                            key="name_gl")
                    g1, g2 = st.columns(2)
                    if g1.button("Save to database", key="save_gl",
                                 disabled=not can_enter):
                        raw_put("sage_gl", name_gl, body_gl,
                                notes=f"{len(gl):,} transactions")
                        st.success(f"Saved as {name_gl}.")
                    if choice_gl != "Upload a new file" and g2.button(
                            "Delete this saved ledger", key="del_gl"):
                        doc_delete("sage_gl", choice_gl)
                        st.success("Deleted. Refresh the page.")
                else:
                    st.warning("No database configured, so this cannot be saved yet.")

elif page == "Cash Flow":
    if not can_configure:
        read_only_note("the forecast assumptions on this page")
    st.write("Cash position projected forward from today using your payment timing rules.")
    saved = load_settings()

    try:
        default_end = date.fromisoformat(str(saved.get("end_date"))[:10])
    except Exception:
        default_end = date(date.today().year, 12, 31)
    if default_end <= date.today():
        default_end = date(date.today().year + 1, 12, 31)

    saved_expenses = load_expense_schedule()
    saved_revenues = load_revenue_schedule()

    with st.form("cash_inputs"):
        a, b = st.columns(2)

        with a:
            st.subheader("Cash In")
            start_cash = st.number_input("Bank cash today ($)", min_value=0.0,
                                         value=float(saved["start_cash"]), step=1000.0)
            revenue = st.number_input("Monthly collections ($)", min_value=0.0,
                                      value=float(saved["revenue"]), step=1000.0)
            revenue_choices = ["Spread evenly", "One day per month",
                               "Enter each month below",
                               "Build from agent hours"]
            revenue_mode = st.radio(
                "Collections timing", revenue_choices,
                index=(revenue_choices.index(saved["revenue_mode"])
                       if saved["revenue_mode"] in revenue_choices else 0),
            )
            revenue_day = st.number_input("Collection day of month", 1, 31,
                                          int(saved["revenue_day"]))
            revenue_lag = st.number_input(
                "Months between billing and collection", 0, 6,
                int(saved.get("revenue_lag", 0)),
                help="Zero means work billed this month is collected this "
                     "month. One means it arrives the following month.")
            end_date = st.date_input(
                "Project through", value=default_end,
                min_value=date.today() + timedelta(days=1),
                help="Runs to this date, so pick a month end and no month "
                     "gets cut in half.")

        with b:
            st.subheader("Cash Out (monthly totals)")
            payroll = st.number_input("Payroll — split 15th and month end ($)", min_value=0.0,
                                      value=float(saved["payroll"]), step=1000.0)
            st.caption(
                "This is a whole month of payroll. It is paid in two halves, so "
                + MONEY.format(payroll / 2) + " on the 15th and "
                + MONEY.format(payroll / 2) + " on the last day."
            )
            css = st.number_input("CSS / government — month end, in arrears ($)", min_value=0.0,
                                  value=float(saved["css"]), step=500.0)
            pluxee = st.number_input("Pluxee bonus — 15th, in arrears ($)", min_value=0.0,
                                     value=float(saved["pluxee"]), step=100.0)
            viatico = st.number_input("Viatico — 15th ($)", min_value=0.0,
                                      value=float(saved["viatico"]), step=100.0)
            decimo = st.number_input(
                "Decimo — 15 Apr, 15 Aug, 15 Dec ($ per payment)", min_value=0.0,
                value=float(saved.get("decimo", 0.0)), step=1000.0)
            fixed = st.number_input("All other fixed expenses ($)", min_value=0.0,
                                    value=float(saved["fixed"]), step=500.0)
            expense_choices = ["Spread evenly", "Use my due-date schedule"]
            expense_mode = st.radio(
                "Other fixed expense timing", expense_choices,
                index=(expense_choices.index(saved.get("expense_mode",
                                                       "Spread evenly"))
                       if saved.get("expense_mode") in expense_choices else 0),
                help="The schedule replaces the figure above with your own "
                     "lines, each on the day it comes due.")

        st.subheader("Accounting basis")
        st.caption(
            "Cash basis moves money on the day it actually leaves the bank, so "
            "decimo lands as a lump on 15 April, 15 August and 15 December and "
            "vacation is charged only when someone is paid out. Accrual basis "
            "charges a smooth monthly provision for decimo and vacation the way "
            "your books carry them, and does not charge the lump payments again. "
            "You can switch between the two views after calculating."
        )
        v1, v2 = st.columns(2)
        with v1:
            decimo_provision = st.number_input(
                "Decimo provision per month ($)", min_value=0.0,
                value=float(saved.get("decimo_provision", 0.0)), step=1000.0,
                help="Accrual view only. One third of a decimo payment is the "
                     "usual figure, since each payment covers four months.")
        with v2:
            vacation_provision = st.number_input(
                "Vacation provision per month ($)", min_value=0.0,
                value=float(saved.get("vacation_provision", 0.0)), step=1000.0,
                help="Accrual view only. What you set aside each month for "
                     "vacation you have not paid out yet.")

        st.subheader("Line of Credit")
        st.caption(
            "A draw is cash in, not revenue, so it is tracked separately and adds "
            "to what you owe. Interest is charged in cash at month end on the "
            "outstanding balance."
        )
        l1, l2, l3 = st.columns(3)
        with l1:
            loc_limit = st.number_input("Credit limit ($)", min_value=0.0,
                                        value=float(saved.get("loc_limit", 0.0)),
                                        step=10000.0)
            loc_drawn = st.number_input("Already drawn today ($)", min_value=0.0,
                                        value=float(saved.get("loc_drawn", 0.0)),
                                        step=5000.0)
        with l2:
            loc_rate = st.number_input("Annual interest rate (%)", min_value=0.0,
                                       value=float(saved.get("loc_rate", 0.0)),
                                       step=0.25)
            loc_min_cash = st.number_input(
                "Keep cash above ($)", min_value=0.0,
                value=float(saved.get("loc_min_cash", 0.0)), step=5000.0,
                help="Automatic draws top the account back up to this floor.")
        with l3:
            loc_auto = st.checkbox("Draw automatically when cash runs short",
                                   value=bool(saved.get("loc_auto", True)))
            loc_draw_amount = st.number_input(
                "One-time draw ($)", min_value=0.0,
                value=float(saved.get("loc_draw_amount", 0.0)), step=5000.0,
                help="A specific draw you already plan to take. Leave at zero "
                     "if you only want automatic draws.")
            try:
                default_draw_day = date.fromisoformat(
                    str(saved.get("loc_draw_day"))[:10])
            except Exception:
                default_draw_day = date.today()
            loc_draw_day = st.date_input("One-time draw date",
                                         value=default_draw_day,
                                         min_value=date(2000, 1, 1))

        with st.expander("Expenses with their own due day"):
            st.caption(
                "One row per expense: the monthly amount and the day of the "
                "month it comes due. Rent on the 5th, insurance on the 10th, "
                "and so on. A day past the end of a short month lands on the "
                "last day instead. This is only used when the timing above is "
                "set to the due-date schedule."
            )
            st.caption(
                "Starts and Ends control when a line is in force, written as "
                "2026-10. Leave them blank for always. To cut a cost from "
                "October, put 2026-09 in Ends on the old row and add a new row "
                "at the lower amount starting 2026-10. The months before the "
                "change keep the old figure, which is what makes the history "
                "honest."
            )
            st.caption(
                "Flexible marks a bill you could pay late without trouble, "
                "typically anything on net 30 terms. Nothing is moved "
                "automatically. It is only used to show you which bills sit in "
                "a tight week, on the Dashboard."
            )
            expense_rows = st.data_editor(
                saved_expenses if not saved_expenses.empty else
                pd.DataFrame([
                    {"Expense": "Rent", "Monthly amount": 0.0, "Day of month": 5,
                     "Starts": "", "Ends": "", "Flexible": False},
                    {"Expense": "Utilities", "Monthly amount": 0.0, "Day of month": 10,
                     "Starts": "", "Ends": "", "Flexible": False},
                    {"Expense": "Insurance", "Monthly amount": 0.0, "Day of month": 15,
                     "Starts": "", "Ends": "", "Flexible": False},
                    {"Expense": "Everything else", "Monthly amount": 0.0, "Day of month": 25,
                     "Starts": "", "Ends": "", "Flexible": False},
                ]),
                num_rows="dynamic", use_container_width=True, hide_index=True,
                key="expense_editor",
                disabled=not can_configure,
                column_config={
                    "Monthly amount": st.column_config.NumberColumn(
                        "Monthly amount", format="$%.2f", min_value=0.0),
                    "Day of month": st.column_config.NumberColumn(
                        "Day of month", min_value=1, max_value=31, step=1),
                    "Starts": st.column_config.TextColumn(
                        "Starts", help="First month this applies, as 2026-10. "
                                       "Blank means from the beginning."),
                    "Ends": st.column_config.TextColumn(
                        "Ends", help="Last month this applies, as 2026-09. "
                                     "Blank means it never stops."),
                    "Flexible": st.column_config.CheckboxColumn(
                        "Flexible", help="Tick if you could pay this late "
                                         "without consequence, such as net 30 "
                                         "terms."),
                },
            )
            active_now = saved_expenses[
                saved_expenses.apply(
                    lambda r: expense_active(r, month_label(date.today())),
                    axis=1)] if not saved_expenses.empty else saved_expenses
            if not saved_expenses.empty:
                e1, e2, e3 = st.columns(3)
                e1.metric("Lines in force this month", str(len(active_now)))
                e2.metric("Due this month", MONEY.format(
                    pd.to_numeric(active_now["Monthly amount"],
                                  errors="coerce").fillna(0.0).sum()))
                flex_sum = pd.to_numeric(
                    active_now[active_now["Flexible"] == True]["Monthly amount"],
                    errors="coerce").fillna(0.0).sum() if not active_now.empty else 0.0
                e3.metric("Of that, flexible", MONEY.format(flex_sum))

        with st.expander("Revenue from agent hours"):
            st.caption(
                "Revenue is agents times billable hours times rate. Change the "
                "headcount in any month and the revenue follows it, so a hiring "
                "plan or a lost account shows up in cash. Used when collections "
                "timing is set to build from agent hours."
            )
            saved_agents = load_agent_schedule()
            wanted_a = months_between(date.today(), default_end)
            if saved_agents.empty:
                saved_agents = pd.DataFrame([
                    {"Month": m, "Agents": 115,
                     "Billable hours per agent": 140.0,
                     "Average rate per hour": 19.75} for m in wanted_a
                ])
            else:
                have_a = set(saved_agents["Month"].astype(str))
                last = saved_agents.iloc[-1]
                extra_a = [m for m in wanted_a if m not in have_a]
                if extra_a:
                    saved_agents = pd.concat([saved_agents, pd.DataFrame([
                        {"Month": m, "Agents": last["Agents"],
                         "Billable hours per agent": last["Billable hours per agent"],
                         "Average rate per hour": last["Average rate per hour"]}
                        for m in extra_a
                    ])], ignore_index=True).sort_values("Month")
            agent_rows = st.data_editor(
                saved_agents, num_rows="dynamic", use_container_width=True,
                hide_index=True, key="agent_editor",
                disabled=not can_configure,
                column_config={
                    "Agents": st.column_config.NumberColumn(min_value=0, step=1),
                    "Billable hours per agent": st.column_config.NumberColumn(
                        min_value=0.0, step=1.0),
                    "Average rate per hour": st.column_config.NumberColumn(
                        min_value=0.0, step=0.25, format="$%.2f"),
                },
            )

        with st.expander("Other revenue lines, such as software"):
            st.caption(
                "Revenue outside the core book, month by month. Software sales "
                "carry no extra payroll, so every dollar here flows to cash. "
                "The Stream column names the line, so you can run two or three "
                "of them side by side. These are always included, whatever the "
                "collections timing above is set to."
            )
            saved_extra = load_extra_revenue()
            if saved_extra.empty:
                months_e = months_between(date.today(), default_end)
                saved_extra = pd.DataFrame(
                    [{"Stream": "Software", "Month": m, "Expected amount": 0.0,
                      "Day of month": 20} for m in months_e]
                    + [{"Stream": "Other new revenue", "Month": m,
                        "Expected amount": 0.0, "Day of month": 20}
                       for m in months_e]
                )
            extra_rows = st.data_editor(
                saved_extra, num_rows="dynamic", use_container_width=True,
                hide_index=True, key="extra_editor",
                disabled=not can_configure,
                column_config={
                    "Expected amount": st.column_config.NumberColumn(
                        min_value=0.0, step=1000.0, format="$%.2f"),
                    "Day of month": st.column_config.NumberColumn(
                        min_value=1, max_value=31, step=1),
                },
            )

        with st.expander("Collections month by month"):
            st.caption(
                "Revenue that changes every month goes here. Fill in what you "
                "expect to collect and the day it arrives. This is only used "
                "when collections timing is set to enter each month."
            )
            base_rev = saved_revenues
            wanted = months_between(date.today(), default_end)
            if base_rev.empty:
                base_rev = blank_revenue_schedule(
                    date.today(), default_end, saved["revenue"],
                    int(saved["revenue_day"]))
            else:
                have = set(base_rev["Month"].astype(str))
                extra = [m for m in wanted if m not in have]
                if extra:
                    base_rev = pd.concat([base_rev, pd.DataFrame([
                        {"Month": m, "Expected collections": float(saved["revenue"]),
                         "Day of month": int(saved["revenue_day"])} for m in extra
                    ])], ignore_index=True).sort_values("Month")
            revenue_rows = st.data_editor(
                base_rev, num_rows="dynamic", use_container_width=True,
                hide_index=True, key="revenue_editor",
                disabled=not can_configure,
            )

        c1, c2 = st.columns(2)
        calculate = c1.form_submit_button("Calculate cash flow")
        store = c2.form_submit_button("Save these numbers",
                                      disabled=not can_configure)

    horizon = max((end_date - date.today()).days + 1, 1)

    current = {
        "start_cash": start_cash,
        "revenue": revenue,
        "revenue_mode": revenue_mode,
        "revenue_day": int(revenue_day),
        "revenue_lag": int(revenue_lag),
        "payroll": payroll,
        "css": css,
        "pluxee": pluxee,
        "viatico": viatico,
        "decimo": decimo,
        "fixed": fixed,
        "horizon": int(horizon),
        "end_date": str(end_date),
        "expense_mode": expense_mode,
        "decimo_provision": decimo_provision,
        "vacation_provision": vacation_provision,
        "loc_limit": loc_limit,
        "loc_drawn": loc_drawn,
        "loc_rate": loc_rate,
        "loc_auto": bool(loc_auto),
        "loc_min_cash": loc_min_cash,
        "loc_draw_amount": loc_draw_amount,
        "loc_draw_day": str(loc_draw_day),
    }

    if store:
        save_settings(current)
        save_expense_schedule(pd.DataFrame(expense_rows))
        save_revenue_schedule(pd.DataFrame(revenue_rows))
        save_agent_schedule(pd.DataFrame(agent_rows))
        save_extra_revenue(pd.DataFrame(extra_rows))
        st.success("Saved. These numbers will load automatically next time.")

    if calculate or store:
        sev = scheduled_payments(load_terminations())
        days = int(horizon)
        exp_frame = pd.DataFrame(expense_rows)
        rev_frame = pd.DataFrame(revenue_rows)
        agent_frame = pd.DataFrame(agent_rows)
        extra_frame = pd.DataFrame(extra_rows)
        if current["revenue_mode"] == "Build from agent hours":
            rev_frame = agent_revenue_schedule(
                agent_frame, int(revenue_day), int(revenue_lag))
            billed = agent_billing(agent_frame)
            st.subheader("Revenue built from agent hours")
            money_table(billed[["Month", "Agents", "Billable hours per agent",
                                "Average rate per hour", "Billed"]])
            first = billed.iloc[0] if not billed.empty else None
            if first is not None:
                st.caption(
                    f"{int(first['Agents'])} agents at "
                    f"{first['Billable hours per agent']:g} hours and "
                    + MONEY.format(first["Average rate per hour"])
                    + " an hour bills " + MONEY.format(first["Billed"])
                    + f" in {first['Month']}, collected on day "
                    f"{int(revenue_day)} "
                    + ("of the same month." if int(revenue_lag) == 0 else
                       f"{int(revenue_lag)} month(s) later.")
                )

        basis_options = ["Cash basis", "Accrual basis"]
        basis = st.radio(
            "View", basis_options, horizontal=True,
            index=(basis_options.index(saved["basis"])
                   if saved.get("basis") in basis_options else 0),
            help="Cash basis pays decimo as a lump on the 15th of April, "
                 "August and December and ignores vacation until it is paid. "
                 "Accrual basis charges monthly provisions instead.")
        current = dict(current, basis=basis)

        df = build_schedule(current, days, sev, exp_frame, rev_frame,
                            extra_frame)
        other = build_schedule(dict(current, basis=(
            "Accrual basis" if basis == "Cash basis" else "Cash basis")),
            days, sev, exp_frame, rev_frame, extra_frame)
        st.caption(
            f"Ending balance on this basis: {MONEY.format(df['Balance'].iloc[-1])}. "
            f"On the other basis it would be "
            f"{MONEY.format(other['Balance'].iloc[-1])}. Projection runs "
            f"{date.today()} through {end_date}, {days} days."
        )
        d30 = df.head(30)

        m1, m2, m3 = st.columns(3)
        m1.metric("Balance in 30 days", MONEY.format(d30["Balance"].iloc[-1]))
        m2.metric(f"Balance in {days} days", MONEY.format(df["Balance"].iloc[-1]))
        low = df.loc[df["Balance"].idxmin()]
        m3.metric(f"Lowest balance ({days}d)", MONEY.format(low["Balance"]),
                  str(low["Date"]))

        extra_total = df["Other Revenue"].sum()
        if extra_total:
            st.subheader("Other revenue lines")
            by_stream = extra_revenue_totals(extra_frame)
            money_table(by_stream)
            st.caption(
                MONEY.format(extra_total) + " of revenue outside the core book "
                "lands inside this window, carried in its own column so you can "
                "see the core business on its own."
            )

        collections_total = df["Collections"].sum()
        payments = int((df["Collections"] > 0).sum()) if current["revenue_mode"] != "Spread evenly" else 0
        if current["revenue_mode"] in ("Enter each month below",
                                       "Build from agent hours"):
            st.caption(
                f"Collections taken from your month-by-month table, "
                f"{MONEY.format(collections_total)} in total over {days} days."
            )
        elif current["revenue_mode"] == "Spread evenly":
            st.caption(
                f"Collections of {MONEY.format(current['revenue'])} per month spread "
                f"daily, {MONEY.format(collections_total)} over {days} days."
            )
        else:
            st.caption(
                f"Collections of {MONEY.format(current['revenue'])} land on day "
                f"{int(current['revenue_day'])} of each month, {payments} payment(s) "
                f"totalling {MONEY.format(collections_total)} in this window. A "
                "window that ends before that day in the final month will not "
                "include that month's payment."
            )

        drawn_window = df["Credit Draw"].sum()
        end_credit = df["Credit Balance"].iloc[-1]
        interest_total = df["Interest"].sum()
        if loc_limit > 0:
            k1, k2, k3 = st.columns(3)
            k1.metric("Drawn in this window", MONEY.format(drawn_window))
            k2.metric("Credit owed at the end", MONEY.format(end_credit))
            k3.metric("Credit still available",
                      MONEY.format(max(loc_limit - end_credit, 0.0)))
            st.caption(f"Interest paid in this window: "
                       + MONEY.format(interest_total))
            first_draw = df[df["Credit Draw"] > 0]
            if not first_draw.empty:
                st.info(
                    "First draw needed on "
                    f"{first_draw['Date'].iloc[0]} for "
                    + MONEY.format(first_draw["Credit Draw"].iloc[0])
                )
            if end_credit >= loc_limit - 0.01 and loc_limit > 0:
                st.error(
                    "The line of credit is fully drawn before the end of this "
                    "window. Cash beyond that point has nothing left to fall "
                    "back on."
                )

        if low["Balance"] < 0:
            st.error(f"Cash goes negative on {low['Date']}. "
                     "The line of credit cannot cover the gap.")
        elif drawn_window > 0:
            st.warning(
                f"Cash stays positive for the full {days} days, but only by "
                f"drawing {MONEY.format(drawn_window)} on the line of credit."
            )
        else:
            st.success(f"Cash stays positive for the full {days} days with no "
                       "draw on the line of credit.")

        st.subheader("Projected balance")
        st.line_chart(df.set_index("Date")["Balance"])

        st.subheader("Month by month")
        st.caption(
            "The first and last months may be partial, since the projection starts "
            "today. Check Days Counted."
        )
        money_table(monthly_summary(df))

        view = st.radio("Detail view", ["30 days", f"{days} days"], horizontal=True)
        table = d30 if view == "30 days" else df
        table = table[table["Net"] != 0]
        st.dataframe(
            table.style.format({c: MONEY for c in table.columns if c != "Date"}),
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    st.caption("Backup: download your saved numbers in case the app is rebuilt.")
    st.download_button(
        "Download my settings",
        data=json.dumps(current, indent=2),
        file_name="cash_settings.json",
        mime="application/json",
    )
    uploaded = (st.file_uploader("Restore settings from a backup file",
                                type="json")
                if can_configure else None)
    if uploaded is not None:
        save_settings(json.load(uploaded))
        st.success("Restored. Refresh the page to see your numbers.")

elif page == "Daily Log":
    if not can_enter:
        read_only_note("the daily log")
    st.write(
        "What actually happened, day by day. Log deposits and payments as they "
        "hit, and type the real bank balance off your online banking. This page "
        "never changes the forecast. It sits beside it so you can see where you "
        "are running ahead or behind."
    )

    saved = load_settings()
    ledger = load_ledger()
    bank = load_bank()
    choices = category_choices()

    # -- reconcile the log against the bank ---------------------------------
    # The earliest balance you typed is the anchor: it is taken as fact. From
    # there the log is added up and compared against the most recent balance
    # you typed. If the two disagree, something never made it into the log.
    # Nothing is back-solved, or the check would always agree with itself.
    bank_sorted = bank.sort_values("Date") if not bank.empty else bank
    anchor_date, anchor_balance, real_balance, real_date = None, None, None, None
    if not bank_sorted.empty:
        first = bank_sorted.iloc[0]
        anchor_date = str(first["Date"])[:10]
        anchor_balance = float(first["Bank balance"])
        last = bank_sorted.iloc[-1]
        real_date = str(last["Date"])[:10]
        real_balance = float(last["Bank balance"])

    if anchor_date is None:
        opening = float(saved.get("start_cash", 0.0))
        counted = ledger
    else:
        opening = anchor_balance
        counted = ledger[ledger["Date"].astype(str) > anchor_date]

    running = ledger_running(counted, opening)
    ledger_balance = float(running["Balance"].iloc[-1]) if not running.empty \
        else opening

    c1, c2, c3 = st.columns(3)
    c1.metric("Balance from the log", MONEY.format(ledger_balance),
              help="Starts at the earliest bank balance you typed, then adds "
                   "and subtracts everything logged after that date.")
    if real_balance is None:
        c2.metric("Bank says", "not entered")
        c3.metric("Difference", "-")
        st.info(
            "The log is running off the starting balance from the Cash Flow "
            "page, " + MONEY.format(opening) + ". Type a real bank balance "
            "below and this becomes a proper reconciliation."
        )
    elif real_date == anchor_date:
        c2.metric("Bank says", MONEY.format(real_balance))
        c3.metric("Difference", "-")
        st.info(
            "Only one bank balance on file, from " + anchor_date + ", so "
            "there is nothing to check it against yet. Type today's balance "
            "and the two will be compared."
        )
    else:
        c2.metric("Bank says", MONEY.format(real_balance),
                  help="Your most recent typed balance, from " + real_date)
        gap = real_balance - ledger_balance
        c3.metric("Difference", MONEY.format(gap))
        if abs(gap) >= 0.01:
            st.warning(
                "The log and the bank disagree by " + MONEY.format(gap)
                + " as of " + real_date + ". "
                + ("The bank holds more than the log explains, so a deposit is "
                   "probably missing from the log."
                   if gap > 0 else
                   "The bank holds less than the log explains, so a payment, "
                   "fee or transfer is probably missing from the log.")
            )
        else:
            st.success("Log and bank agree as of " + real_date + ".")
        st.caption(
            "Anchored on " + MONEY.format(anchor_balance) + " at "
            + anchor_date + ", plus everything logged after it."
        )

    st.divider()

    # -- add one entry -------------------------------------------------------
    # Hidden entirely rather than shown greyed out, since a form you can type
    # into but never save is worse than no form at all.
    if not can_enter:
        st.divider()
        st.caption(
            "Entry forms are hidden because your access level is read-only. "
            "Everything logged is still shown below."
        )
    if can_enter:
      st.subheader("Log something that happened")
      with st.form("daily_entry", clear_on_submit=True):
          f1, f2 = st.columns(2)
          entry_date = f1.date_input("Date", value=date.today())
          entry_cat = f2.selectbox("Category", choices)
          entry_desc = st.text_input(
              "Description", placeholder="Client X deposit, rent for August")
          g1, g2 = st.columns(2)
          amount_in = g1.number_input("Money in", min_value=0.0, step=100.0,
                                      format="%.2f")
          amount_out = g2.number_input("Money out", min_value=0.0, step=100.0,
                                       format="%.2f")
          added = st.form_submit_button("Add to the log", disabled=not can_enter)
      if added:
          if amount_in == 0.0 and amount_out == 0.0:
              st.error("Enter an amount in or out.")
          else:
              ledger = save_ledger(pd.concat([ledger, pd.DataFrame([{
                  "Date": entry_date.isoformat(),
                  "Description": entry_desc,
                  "Category": entry_cat,
                  "Money in": float(amount_in),
                  "Money out": float(amount_out),
              }])], ignore_index=True))
              st.success("Logged. " + MONEY.format(
                  amount_in if amount_in else amount_out) + " "
                  + ("in" if amount_in else "out") + ".")
              st.rerun()

      st.subheader("Today's bank balance")
      with st.form("bank_entry", clear_on_submit=True):
          b1, b2 = st.columns(2)
          bank_date = b1.date_input("As of", value=date.today(), key="bankdate")
          bank_amount = b2.number_input("Balance in the account", step=100.0,
                                        format="%.2f")
          bank_note = st.text_input("Note", placeholder="optional")
          saved_bank = st.form_submit_button("Save this balance",
                                             disabled=not can_enter)
      if saved_bank:
          bank = save_bank(pd.concat([bank, pd.DataFrame([{
              "Date": bank_date.isoformat(),
              "Bank balance": float(bank_amount),
              "Note": bank_note,
          }])], ignore_index=True))
          st.success("Saved.")
          st.rerun()

    st.divider()

    # -- the log itself ------------------------------------------------------
    st.subheader("The log")
    if ledger.empty:
        st.info("Nothing logged yet. Add your first entry above.")
    else:
        edited = st.data_editor(
            ledger, num_rows="dynamic", use_container_width=True,
            hide_index=True, key="ledger_editor",
            disabled=not can_enter,
            column_config={
                "Date": st.column_config.TextColumn(
                    "Date", help="YYYY-MM-DD"),
                "Category": st.column_config.SelectboxColumn(
                    "Category", options=choices),
                "Money in": st.column_config.NumberColumn(
                    "Money in", format="$%.2f", min_value=0.0),
                "Money out": st.column_config.NumberColumn(
                    "Money out", format="$%.2f", min_value=0.0),
            },
        )
        if st.button("Save changes to the log", disabled=not can_enter):
            save_ledger(edited)
            st.success("Saved.")
            st.rerun()

        tin = pd.to_numeric(ledger["Money in"], errors="coerce").fillna(0.0).sum()
        tout = pd.to_numeric(ledger["Money out"], errors="coerce").fillna(0.0).sum()
        m1, m2, m3 = st.columns(3)
        m1.metric("Logged in", MONEY.format(tin))
        m2.metric("Logged out", MONEY.format(tout))
        m3.metric("Net", MONEY.format(tin - tout))

    if not bank.empty:
        with st.expander("Bank balances you have typed"):
            edited_bank = st.data_editor(
                bank, num_rows="dynamic", use_container_width=True,
                hide_index=True, key="bank_editor",
                disabled=not can_enter,
                column_config={
                    "Bank balance": st.column_config.NumberColumn(
                        "Bank balance", format="$%.2f"),
                },
            )
            if st.button("Save bank balances", disabled=not can_enter):
                save_bank(edited_bank)
                st.success("Saved.")
                st.rerun()

    st.divider()

    # -- forecast beside actual ---------------------------------------------
    st.subheader("Forecast beside actual")
    sev = scheduled_payments(load_terminations())
    try:
        target = date.fromisoformat(str(saved.get("end_date"))[:10])
        days = max((target - date.today()).days + 1, 30)
    except Exception:
        days = int(saved.get("horizon", 120))
    if saved.get("revenue_mode") == "Build from agent hours":
        rev_frame = agent_revenue_schedule(
            load_agent_schedule(), int(saved.get("revenue_day", 20)),
            int(saved.get("revenue_lag", 0)))
    else:
        rev_frame = load_revenue_schedule()
    plan = build_schedule(saved, days, sev, load_expense_schedule(),
                          rev_frame, load_extra_revenue())
    plan_monthly = monthly_summary(plan)

    actual_monthly = ledger_by_month(ledger)
    if actual_monthly.empty:
        st.info(
            "Once you have logged a few entries, this will show the forecast "
            "for the month beside what really happened, line by line."
        )
    else:
        months = sorted(set(actual_monthly["Month"]))
        pick = st.selectbox("Month", months, index=len(months) - 1)
        table = variance_table(
            actual_monthly[actual_monthly["Month"] == pick],
            plan_monthly, pick)
        if table.empty:
            st.info("Nothing to compare for that month yet.")
        else:
            money_table(table)
            covered = plan_monthly[plan_monthly["Month"] == pick]
            days_counted = int(covered["Days Counted"].iloc[0]) \
                if not covered.empty else 0
            month_len = calendar.monthrange(
                int(pick[:4]), int(pick[5:7]))[1]
            st.caption(
                "Difference is actual less forecast. On a revenue line a "
                "positive number is good. On an expense line a positive "
                "number means you spent more than planned."
            )
            if 0 < days_counted < month_len:
                st.warning(
                    "Read this one carefully. The forecast only projects "
                    "forward from today, so the forecast column for " + pick
                    + " covers " + str(days_counted) + " of "
                    + str(month_len) + " days, while your log may cover the "
                    "whole month. Anything already paid before today sits in "
                    "the actual column with no forecast beside it. The "
                    "comparison is only apples to apples for a month that "
                    "starts after today."
                )

        # Where the month is heading: real money so far, forecast for the rest.
        month_rows = ledger[ledger["Date"].astype(str).str.slice(0, 7) == pick]
        last_logged = str(month_rows["Date"].max())[:10] if not month_rows.empty \
            else None
        if last_logged:
            rest = plan[pd.to_datetime(plan["Date"]).dt.strftime("%Y-%m-%d")
                        > last_logged]
            rest = rest[pd.to_datetime(rest["Date"]).dt.strftime("%Y-%m") == pick]
            in_so_far = pd.to_numeric(
                month_rows["Money in"], errors="coerce").fillna(0.0).sum()
            out_so_far = pd.to_numeric(
                month_rows["Money out"], errors="coerce").fillna(0.0).sum()
            still_in = float(rest["Collections"].sum()
                             + rest["Other Revenue"].sum()) if not rest.empty else 0.0
            still_out = float(
                rest["Payroll"].sum() + rest["CSS / Government"].sum()
                + rest["Pluxee"].sum() + rest["Viatico"].sum()
                + rest["Decimo"].sum() + rest["Severance"].sum()
                + rest["Other Fixed"].sum() + rest["Interest"].sum()
            ) if not rest.empty else 0.0
            st.markdown("**Where this month lands**")
            w1, w2, w3 = st.columns(3)
            w1.metric("Logged so far", MONEY.format(in_so_far - out_so_far),
                      help="Money in less money out, from the log only.")
            w2.metric("Forecast for the rest",
                      MONEY.format(still_in - still_out),
                      help="From the day after your last entry to month end.")
            w3.metric("Month should end at",
                      MONEY.format((in_so_far - out_so_far)
                                   + (still_in - still_out)))
            st.caption(
                "Actuals run through " + last_logged
                + ". The rest comes from the forecast."
            )

    if not running.empty:
        st.divider()
        st.subheader("Actual balance over time")
        st.line_chart(running.set_index("Date")["Balance"])
        with st.expander("Day by day"):
            money_table(running)

elif page == "Accounts":
    if not can_configure:
        st.error("Only an administrator can open this page.")
        st.stop()
    st.write(
        "Up to " + str(MAX_USERS) + " accounts. Passwords are stored as a "
        "one-way hash, so nobody can read them back, including me. If someone "
        "forgets theirs, issue a temporary one here and they will be made to "
        "replace it the moment they sign in."
    )
    users = load_users()
    st.subheader("What each level can do")
    st.dataframe(
        pd.DataFrame([{"Level": r, "Can do": ROLE_HELP[r]} for r in ROLES]),
        use_container_width=True, hide_index=True,
    )

    st.subheader("Accounts")
    if not users:
        st.info("No accounts yet.")
    else:
        st.dataframe(
            pd.DataFrame([{
                "Username": u.get("login", ""),
                "Name": u.get("name", ""),
                "Email": u.get("email", ""),
                "Level": u.get("role", "viewer"),
                "Must change password": bool(u.get("must_change", False)),
            } for u in users]),
            use_container_width=True, hide_index=True,
        )

    st.subheader("Add an account")
    if len(users) >= MAX_USERS:
        st.info(
            "You already have " + str(MAX_USERS) + " accounts, which is the "
            "limit you asked for. Remove one before adding another."
        )
    else:
        with st.form("add_user"):
            a1, a2 = st.columns(2)
            with a1:
                new_login = st.text_input("Username")
                new_name = st.text_input("Full name")
            with a2:
                new_email = st.text_input("Email (optional)")
                new_role = st.selectbox("Access level", ["user", "viewer"],
                                        help="Only one administrator is "
                                             "needed. Change an existing "
                                             "account below if that must move.")
            temp_a = st.text_input("Temporary password", type="password")
            temp_b = st.text_input("Temporary password again", type="password")
            add = st.form_submit_button("Create account")
        if add:
            login_clean = str(new_login).strip().lower()
            if not login_clean:
                st.error("A username is required.")
            elif find_user(users, login_clean):
                st.error("That username is already taken.")
            elif new_email and find_user(users, new_email):
                st.error("That email is already used by another account.")
            elif len(temp_a) < 10:
                st.error("Use at least 10 characters for the temporary password.")
            elif temp_a != temp_b:
                st.error("Those two do not match.")
            else:
                users.append({
                    "login": login_clean,
                    "name": str(new_name).strip() or login_clean,
                    "email": str(new_email).strip(),
                    "role": new_role,
                    "password": hash_password(temp_a),
                    "must_change": True,
                })
                save_users(users)
                st.success(
                    "Created " + login_clean + " as " + new_role + ". Give them "
                    "that temporary password in person or by phone, not in "
                    "writing, and they will be asked to change it on their "
                    "first sign-in."
                )
                st.rerun()

    if users:
        st.subheader("Change an account")
        who = st.selectbox("Account", [u.get("login", "") for u in users],
                           key="edit_which")
        target = find_user(users, who)
        admins = [u for u in users if u.get("role") == "admin"]
        last_admin = (target.get("role") == "admin" and len(admins) <= 1)
        with st.form("edit_user"):
            e1, e2 = st.columns(2)
            with e1:
                ed_name = st.text_input("Full name", value=target.get("name", ""))
                ed_email = st.text_input("Email", value=target.get("email", ""))
            with e2:
                ed_role = st.selectbox(
                    "Access level", ROLES,
                    index=ROLES.index(target.get("role", "viewer")),
                    disabled=last_admin,
                    help="Locked, because this is the only administrator left."
                         if last_admin else ROLE_HELP.get(
                             target.get("role", "viewer"), ""))
            reset_a = st.text_input("Set a temporary password (leave blank to "
                                    "keep the current one)", type="password")
            apply_it = st.form_submit_button("Apply changes")
        if apply_it:
            if reset_a and len(reset_a) < 10:
                st.error("Use at least 10 characters.")
            else:
                target["name"] = str(ed_name).strip() or target.get("login", "")
                target["email"] = str(ed_email).strip()
                if not last_admin:
                    target["role"] = ed_role
                if reset_a:
                    target["password"] = hash_password(reset_a)
                    target["must_change"] = True
                save_users(users)
                st.success("Updated " + str(target.get("login", "")) + ".")
                st.rerun()

        st.subheader("Remove an account")
        gone = st.selectbox(
            "Account to remove",
            [u.get("login", "") for u in users
             if u.get("login", "") != USER.get("login")],
            key="remove_which") if len(users) > 1 else None
        if gone is None:
            st.caption("There is nothing to remove. You cannot delete the "
                       "account you are signed in with.")
        else:
            sure = st.checkbox("Yes, remove " + str(gone), key="confirm_remove")
            if st.button("Remove account", disabled=not sure):
                left = [u for u in users
                        if str(u.get("login", "")).lower() != str(gone).lower()]
                if not any(u.get("role") == "admin" for u in left):
                    st.error("That would leave no administrator. Promote "
                             "someone else first.")
                else:
                    save_users(left)
                    st.success("Removed " + str(gone) + ".")
                    st.rerun()

elif page == "AI Assistant":
    st.write("Internal performance assistant.")
    st.info("Assistant will be added here.")
