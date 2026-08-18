import streamlit as st

st.set_page_config(page_title="7MS Forecasting Tool", page_icon="📈", layout="wide")

st.sidebar.title("7MS Forecasting Tool")
page = st.sidebar.radio(
    "Go to",
    [
        "Dashboard",
        "Forecast",
        "Payroll",
        "Sage Actuals",
        "Cash Flow",
        "AI Assistant",
    ],
)

st.title(page)

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
    st.write("30-day and 90-day cash flow views.")
    st.info("Daily bank cash input will be added here.")

elif page == "AI Assistant":
    st.write("Internal performance assistant.")
    st.info("Assistant will be added here.")
