import calendar
from datetime import date, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="7MS Forecasting Tool", page_icon="📈", layout="wide")

st.sidebar.title("7MS Forecasting Tool")
page = st.sidebar.radio(
    "Go to",
    ["Dashboard", "Forecast", "Payroll", "Sage Actuals", "Cash Flow", "AI Assistant"],
)

st.title(page)


def days_in_month(d):
    return calendar.monthrange(d.year, d.month)[1]


def is_last_day(d):
    return d.day == days_in_month(d)


def build_schedule(start_cash, revenue, revenue_mode, revenue_day, payroll,
                   css, pluxee, viatico, fixed, horizon):
    start = date.today()
    balance = start_cash
    rows = []
    for i in range(horizon):
        d = start + timedelta(days=i)
        dim = days_in_month(d)

        if revenue_mode == "Spread evenly":
            collections = revenue / dim
        else:
            collections = revenue if d.day == min(revenue_day, dim) else 0.0

        pay = payroll / 2 if (d.day == 15 or is_last_day(d)) else 0.0
        css_out = css if is_last_day(d) else 0.0
        pluxee_out = pluxee if d.day == 15 else 0.0
        viatico_out = viatico if d.day == 15 else 0.0
        fixed_out = fixed / dim

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


if page == "Dashboard":
    st.write("Overview of forecast vs actual, cash position, and payroll.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Cash on hand", "$0.00")
    c2.metric("Forecast this month", "$0.00")
    c3.metric("Payroll this month", "$0.00")

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

    with st.form("cash_inputs"):
        a, b = st.columns(2)

        with a:
            st.subheader("Cash In")
            start_cash = st.number_input("Bank cash today ($)", min_value=0.0, step=1000.0)
            revenue = st.number_input("Monthly collections ($)", min_value=0.0, step=1000.0)
            revenue_mode = st.radio("Collections timing", ["Spread evenly", "One day per month"])
            revenue_day = st.number_input("Collection day of month", 1, 31, 10)

        with b:
            st.subheader("Cash Out (monthly totals)")
            payroll = st.number_input("Payroll — split 15th and month end ($)", min_value=0.0, step=1000.0)
            css = st.number_input("CSS / government — month end, in arrears ($)", min_value=0.0, step=500.0)
            pluxee = st.number_input("Pluxee bonus — 15th, in arrears ($)", min_value=0.0, step=100.0)
            viatico = st.number_input("Viatico — 15th ($)", min_value=0.0, step=100.0)
            fixed = st.number_input("All other fixed expenses ($)", min_value=0.0, step=500.0)

        submitted = st.form_submit_button("Calculate cash flow")

    if submitted:
        df = build_schedule(start_cash, revenue, revenue_mode, int(revenue_day),
                            payroll, css, pluxee, viatico, fixed, 90)

        d30 = df.head(30)
        d90 = df

        m1, m2, m3 = st.columns(3)
        m1.metric("Balance in 30 days", f"${d30['Balance'].iloc[-1]:,.2f}")
        m2.metric("Balance in 90 days", f"${d90['Balance'].iloc[-1]:,.2f}")
        low = d90.loc[d90["Balance"].idxmin()]
        m3.metric("Lowest balance (90d)", f"${low['Balance']:,.2f}", str(low["Date"]))

        if low["Balance"] < 0:
            st.error(f"Cash goes negative on {low['Date']}. Review collections timing or expenses.")
        else:
            st.success("Cash stays positive for the full 90 days.")

        st.subheader("Projected balance")
        st.line_chart(df.set_index("Date")["Balance"])

        view = st.radio("Detail view", ["30 days", "90 days"], horizontal=True)
        table = d30 if view == "30 days" else d90
        table = table[table["Net"] != 0]
        st.dataframe(
            table.style.format({c: "${:,.2f}" for c in table.columns if c != "Date"}),
            use_container_width=True,
            hide_index=True,
        )

elif page == "AI Assistant":
    st.write("Internal performance assistant.")
    st.info("Assistant will be added here.")
