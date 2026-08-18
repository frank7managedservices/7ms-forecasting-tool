import calendar
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from payroll import read_payroll, summarize, by_group, loan_detail

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
    "decimo": 0.0,
    "fixed": 0.0,
}

MONEY = "${:,.2f}"


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            return {**DEFAULTS, **json.loads(SETTINGS_FILE.read_text())}
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
        decimo_out = s.get("decimo", 0.0) if (d.day == 15 and d.month in (4, 8, 12)) else 0.0
        viatico_out = s["viatico"] if d.day == 15 else 0.0
        fixed_out = s["fixed"] / dim

        out = pay + css_out + pluxee_out + viatico_out + decimo_out + fixed_out
        balance += collections - out

        rows.append({
            "Date": d,
            "Collections": collections,
            "Payroll": pay,
            "CSS / Government": css_out,
            "Pluxee": pluxee_out,
            "Viatico": viatico_out,
            "Decimo": decimo_out,
            "Other Fixed": fixed_out,
            "Net": collections - out,
            "Balance": balance,
        })
    return pd.DataFrame(rows)


def money_table(df):
    fmt = {c: MONEY for c in df.columns if df[c].dtype.kind in "fi" and c != "Headcount"}
    st.dataframe(df.style.format(fmt), use_container_width=True, hide_index=True)


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
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cash on hand", MONEY.format(saved["start_cash"]))
    c2.metric("Balance in 30 days", MONEY.format(df["Balance"].iloc[29]))
    c3.metric("Balance in 90 days", MONEY.format(df["Balance"].iloc[-1]))
    c4.metric("Payroll this month", MONEY.format(saved["payroll"]))
    st.caption(
        "Decimo per payment: " + MONEY.format(saved.get("decimo", 0.0))
        + "  ·  paid 15 April, 15 August, 15 December"
    )
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

    upload = st.file_uploader("DV Pre-Planilla file", type=["xlsx", "xls", "csv"])

    if upload is None:
        st.info("Choose a file to see headcount, pay, and employer cost by employee group.")
    else:
        try:
            df, meta = read_payroll(upload, upload.name)
        except Exception as exc:
            st.error(f"Could not read that file: {exc}")
            st.stop()

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
        st.caption("These come out of employee pay and are not company operating expenses.")
        loans = loan_detail(df)
        if loans.empty:
            st.write("No loan deductions in this period.")
        else:
            money_table(loans)

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
        st.caption(
            "This period is one quincena, so recurring monthly figures are double the "
            "period amount. Decimo is not doubled: it is sent as a separate three-times-"
            "a-year payment."
        )
        recurring_net = s["net"] - s["decimo_paid"]
        monthly_payroll = recurring_net * 2
        monthly_css = (s["employer_cost"] + s["employee_statutory"]) * 2
        i, j, k2 = st.columns(3)
        i.metric("Monthly payroll cash", MONEY.format(monthly_payroll))
        j.metric("Monthly CSS / government", MONEY.format(monthly_css))
        k2.metric("Decimo per payment", MONEY.format(s["decimo_paid"]))
        send_decimo = st.checkbox(
            "Also update the decimo amount",
            value=s["decimo_paid"] > 0,
            help="Only tick this when the uploaded period actually contains decimo.",
        )
        if st.button("Update my Cash Flow numbers"):
            saved = load_settings()
            saved["payroll"] = round(monthly_payroll, 2)
            saved["css"] = round(monthly_css, 2)
            if send_decimo:
                saved["decimo"] = round(s["decimo_paid"], 2)
            save_settings(saved)
            st.success("Cash Flow updated. Open the Cash Flow page to see the effect.")

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
            decimo = st.number_input(
                "Decimo — 15 Apr, 15 Aug, 15 Dec ($ per payment)", min_value=0.0,
                value=float(saved.get("decimo", 0.0)), step=1000.0)
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
        "decimo": decimo,
        "fixed": fixed,
    }

    if store:
        save_settings(current)
        st.success("Saved. These numbers will load automatically next time.")

    if calculate or store:
        df = build_schedule(current, 90)
        d30 = df.head(30)

        m1, m2, m3 = st.columns(3)
        m1.metric("Balance in 30 days", MONEY.format(d30["Balance"].iloc[-1]))
        m2.metric("Balance in 90 days", MONEY.format(df["Balance"].iloc[-1]))
        low = df.loc[df["Balance"].idxmin()]
        m3.metric("Lowest balance (90d)", MONEY.format(low["Balance"]), str(low["Date"]))

        if low["Balance"] < 0:
            st.error(f"Cash goes negative on {low['Date']}. Review collections timing or expenses.")
        else:
            st.success("Cash stays positive for the full 90 days.")

        st.subheader("Projected balance")
        st.line_chart(df.set_index("Date")["Balance"])

        view = st.radio("Detail view", ["30 days", "90 days"], horizontal=True)
        table = df if view == "90 days" else d30
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
