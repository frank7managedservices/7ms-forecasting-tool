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

EXPENSE_COLUMNS = ["Expense", "Monthly amount", "Day of month"]
REVENUE_COLUMNS = ["Month", "Expected collections", "Day of month"]


def load_expense_schedule():
    rows = kv_get("expense_schedule")
    if isinstance(rows, list) and rows:
        return pd.DataFrame(rows)[EXPENSE_COLUMNS]
    return pd.DataFrame(columns=EXPENSE_COLUMNS)


def save_expense_schedule(frame):
    frame = frame.dropna(subset=["Expense"])
    frame = frame[frame["Expense"].astype(str).str.strip() != ""]
    kv_put("expense_schedule", frame.to_dict("records"))
    return frame


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
    if use_expense_schedule:
        due = {}
        for _, r in expenses.iterrows():
            try:
                day = int(r["Day of month"])
                amount = float(r["Monthly amount"])
            except Exception:
                continue
            due[max(1, min(day, 31))] = due.get(max(1, min(day, 31)), 0.0) + amount

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


st.sidebar.title("7MS Forecasting Tool")
page = st.sidebar.radio(
    "Go to",
    ["Dashboard", "Forecast", "Payroll", "Terminations", "Sage Actuals",
     "Cash Flow", "AI Assistant"],
)

storage_note()

st.title(page)

if page == "Dashboard":
    st.write("Overview of forecast vs actual, cash position, and payroll.")
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
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cash on hand", MONEY.format(saved["start_cash"]))
    c2.metric("Balance in 30 days", MONEY.format(df["Balance"].iloc[min(29, days - 1)]))
    c3.metric(f"Balance in {days} days", MONEY.format(df["Balance"].iloc[-1]))
    c4.metric("Payroll this month", MONEY.format(saved["payroll"]))
    st.caption(
        "Decimo per payment: " + MONEY.format(saved.get("decimo", 0.0))
        + "  ·  paid 15 April, 15 August, 15 December"
    )
    if saved.get("loc_limit", 0.0) > 0:
        drawn_end = df["Credit Balance"].iloc[-1]
        st.caption(
            "Line of credit: " + MONEY.format(drawn_end) + " owed at day "
            + str(days) + " of " + MONEY.format(saved["loc_limit"]) + " limit  ·  "
            + MONEY.format(df["Credit Draw"].sum()) + " drawn in this window"
        )
    due = df["Severance"].sum()
    if due:
        st.warning(f"Severance due in the next {days} days: {MONEY.format(due)}")
    st.line_chart(df.set_index("Date")["Balance"])

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
    st.caption(
        f"Multiplier in use: {per_month:g}. Net pay in this file is "
        + MONEY.format(s["net"]) + ", so monthly payroll cash is "
        + MONEY.format(s["net"] * per_month) + "."
    )

    monthly_payroll = s["net"] * per_month
    monthly_css = (s["employer_cost"] + s["employee_statutory"]) * per_month
    monthly_viatico = s["viatico"] * per_month
    # Decimo is one month of pay a year paid in three installments, so each
    # installment covers four months of accrual.
    decimo_payment = s["decimo_accrued"] * per_month * 4

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
    regular_payroll = (s["net"] - tot["benefit_cash_total"]) * per_month
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
    if bc1.button("Send cash basis numbers"):
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
    if bc2.button("Send accrual basis numbers"):
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
    st.info(
        "One caution on cash basis: net pay already contains "
        + MONEY.format(tot["decimo_cash"] * per_month)
        + " a month of decimo advances paid through the planilla. If you also "
        "charge the full decimo lump, that much is counted twice. Reduce the "
        "decimo figure on the Cash Flow page if these advances are common."
    )

    st.divider()
    st.subheader("Save this period")
    if db_ready():
        default_name = str(meta.get("Detalle") or "period").strip()[:120]
        label = st.text_input("Name for this period", value=default_name)
        s1, s2 = st.columns(2)
        if s1.button("Save to database"):
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
        if st.button("Add to upcoming payments"):
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
                    disabled=["Line", "Sage monthly average"],
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
                if u1.button("Use the Sage average as my other fixed expenses"):
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
                    if b1.button("Save to database", key="save_is"):
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
                    if g1.button("Save to database", key="save_gl"):
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
            expense_rows = st.data_editor(
                saved_expenses if not saved_expenses.empty else
                pd.DataFrame([
                    {"Expense": "Rent", "Monthly amount": 0.0, "Day of month": 5},
                    {"Expense": "Utilities", "Monthly amount": 0.0, "Day of month": 10},
                    {"Expense": "Insurance", "Monthly amount": 0.0, "Day of month": 15},
                    {"Expense": "Everything else", "Monthly amount": 0.0, "Day of month": 25},
                ]),
                num_rows="dynamic", use_container_width=True, hide_index=True,
                key="expense_editor",
            )

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
            )

        c1, c2 = st.columns(2)
        calculate = c1.form_submit_button("Calculate cash flow")
        store = c2.form_submit_button("Save these numbers")

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
    uploaded = st.file_uploader("Restore settings from a backup file", type="json")
    if uploaded is not None:
        save_settings(json.load(uploaded))
        st.success("Restored. Refresh the page to see your numbers.")

elif page == "AI Assistant":
    st.write("Internal performance assistant.")
    st.info("Assistant will be added here.")
