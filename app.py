import calendar
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="7MS Forecasting Tool", page_icon="📈", layout="wide")

SETTINGS_FILE = Path("cash_settings.json")

DEFAULTS = {
    "start_cash": 0.0,
    "revenue": 0.0,
    "revenue_mode": "Spread evenly",
    "revenue_day": 10,
    "payroll": 0.0,
    "css": 0.0,
    "pluxee": 0.0,
    "viatico": 0.0,
    "fixed": 0.0,
}


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text())
            return {**DEFAULTS, **saved}
        except Exception:
            return dict(DEFAULTS)
    return dict(DEFAULTS)


def save_settings(values):
    SETTINGS_FILE.write_text(json.dumps(values, indent=2))


def days_in_month(d):
    return calendar.monthrange(d.year, d.month)[1]


def is_last_day(d):
    return d.day == days_in_month(d)


def build_schedule(s, horizon):
    start = date.today()
    balance = s["start_cash"]
    rows = []
    for i in range(horizon):
        d = start + timedelta(days=i)
        dim = days_in_month(d)

        if s["revenue_mode"] == "Spread evenly":
            collections = s["revenue"] / dim
        else:
            collections = s["revenue"] if d.day == min(s["revenue_day"], dim) else 0.0

        pay = s["payroll"] / 2 if (d.day == 15 or is_last_day(d)) else 0.0
        css_out = s["css"] if is_last_day(d) else 0.0
        pluxee_out = s["pluxee"] if d.day == 15 else 0.0
        viatico_out = s["viatico"] if d.day == 15 else 0.0
        fixed_out = s["fixed"] / dim

        out = pay + css_out + pluxee_out + viatico_out + fixed_out
        balance += collections - out

        rows.append({
            "Date": d,
            "Collections": collections,
            "Payroll": pay,
            "CSS / Government": css_out,
            "Pluxee": pluxee_out,
            "Viatico": viatico_out,
            "Other Fixed": fixed_out,
            "Net": collections - out,
            "Balance": balance,
        })
    return pd.DataFrame(rows)


st.sidebar.title("7MS Forecasting Tool")
page = st.sidebar.radio(
    "Go to",
    ["Dashboard", "Forecast", "Payroll", "Sage Actuals", "Cash Flow", "AI Assistant"],
)

st.title(page)

if page == "Dashboard":
    st.write("Overview of forecast vs actual, cash position, and payroll.")
    saved = load_settings()
    df = build_schedule(saved, 90)
    c1, c2, c3 = st.columns(3)
    c1.metric("Cash on hand", f"${saved['start_cash']:,.2f}")
    c2.metric("Balance in 30 days", f"${df['Balance'].iloc[29]:,.2f}")
    c3.metric("Payroll this month", f"${saved['payroll']:,.2f}")

elif page == "Forecast":
    st.write("Monthly forecasting by employee group.")
    revenue = st.number_input("Current monthly revenue ($)", min_value=0.0, step=100.0)
    growth = st.number_input("Expected monthly growth rate (%)", value=0.0, step=0.1)
    months = st.slider("Months to forecast", 1, 24, 12)
    projected = revenue * (1 + growth / 100) ** months
    st.metric(f"Estimated revenue in {months} months", f"${projected:,.2f}")

elif page == "Payroll":
    st.write("Payroll is paid on the 15th and the final day of each month.")
    st.info("DV Pre-Planilla import will be added here.")

elif page == "Sage Actuals":
    st.write("Sage 50 GL and income statement actuals.")
    st.info("Sage import will be added here.")

elif page == "Cash Flow":
    st.write("Cash position projected forward from today using your payment timing rules.")
    saved = load_settings()

    with st.form("cash_inputs"):
        a, b = st.columns(2)

        with a:
            st.subheader("Cash In")
            start_cash = st.number_input("Bank cash today ($)", min_value=0.0,
                                         value=float(saved["start_cash"]), step=1000.0)
            revenue = st.number_input("Monthly collections ($)", min_value=0.0,
                                      value=float(saved["revenue"]), step=1000.0)
            revenue_mode = st.radio(
                "Collections timing", ["Spread evenly", "One day per month"],
                index=0 if saved["revenue_mode"] == "Spread evenly" else 1,
            )
            revenue_day = st.number_input("Collection day of month", 1, 31,
                                          int(saved["revenue_day"]))

        with b:
            st.subheader("Cash Out (monthly totals)")
            payroll = st.number_input("Payroll — split 15th and month end ($)", min_value=0.0,
                                      value=float(saved["payroll"]), step=1000.0)
            css = st.number_input("CSS / government — month end, in arrears ($)", min_value=0.0,
                                  value=float(saved["css"]), step=500.0)
            pluxee = st.number_input("Pluxee bonus — 15th, in arrears ($)", min_value=0.0,
                                     value=float(saved["pluxee"]), step=100.0)
            viatico = st.number_input("Viatico — 15th ($)", min_value=0.0,
                                      value=float(saved["viatico"]), step=100.0)
            fixed = st.number_input("All other fixed expenses ($)", min_value=0.0,
                                    value=float(saved["fixed"]), step=500.0)

        c1, c2 = st.columns(2)
        calculate = c1.form_submit_button("Calculate cash flow")
        store = c2.form_submit_button("Save these numbers")

    current = {
        "start_cash": start_cash,
        "revenue": revenue,
        "revenue_mode": revenue_mode,
        "revenue_day": int(revenue_day),
        "payroll": payroll,
        "css": css,
        "pluxee": pluxee,
        "viatico": viatico,
        "fixed": fixed,
    }

    if store:
        save_settings(current)
        st.success("Saved. These numbers will load automatically next time.")

    if calculate or store:
        df = build_schedule(current, 90)
        d30 = df.head(30)

        m1, m2, m3 = st.columns(3)
        m1.metric("Balance in 30 days", f"${d30['Balance'].iloc[-1]:,.2f}")
        m2.metric("Balance in 90 days", f"${df['Balance'].iloc[-1]:,.2f}")
        low = df.loc[df["Balance"].idxmin()]
        m3.metric("Lowest balance (90d)", f"${low['Balance']:,.2f}", str(low["Date"]))

        if low["Balance"] < 0:
            st.error(f"Cash goes negative on {low['Date']}. Review collections timing or expenses.")
        else:
            st.success("Cash stays positive for the full 90 days.")

        st.subheader("Projected balance")
        st.line_chart(df.set_index("Date")["Balance"])

        view = st.radio("Detail view", ["30 days", "90 days"], horizontal=True)
        table = d30 if view == "30 days" else df
        table = table[table["Net"] != 0]
        st.dataframe(
            table.style.format({c: "${:,.2f}" for c in table.columns if c != "Date"}),
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
