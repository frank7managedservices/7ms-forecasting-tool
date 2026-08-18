import streamlit as st

st.set_page_config(page_title="7MS Forecasting Tool", page_icon="📈")

st.title("7MS Forecasting Tool")
st.write("Create and review simple business forecasts.")

monthly_revenue = st.number_input(
    "Current monthly revenue ($)",
    min_value=0.0,
    value=0.0,
    step=100.0
)

monthly_growth = st.number_input(
    "Expected monthly growth rate (%)",
    value=0.0,
    step=0.1
)

months = st.slider(
    "Months to forecast",
    min_value=1,
    max_value=24,
    value=12
)

forecast_revenue = monthly_revenue * (1 + monthly_growth / 100) ** months

st.subheader("Forecast")
st.metric(
    f"Estimated revenue in {months} months",
    f"${forecast_revenue:,.2f}"
)
