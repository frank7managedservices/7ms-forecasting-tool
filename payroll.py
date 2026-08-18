"""Payroll import logic for the 7MS Forecasting Tool."""

import pandas as pd

LOAN_COLUMNS = ["ADESAL", "ART222", "BANIST", "CXCEMP", "FINPEY", "FINSOL", "PRESPA"]

GROUP_MAP = {
    "ASSOCIATE": "Agents",
    "ASOCIADO": "Agents",
    "AGENT": "Agents",
    "AGENT TRAINEE": "Agent Trainee",
    "SUPERVISOR": "Supervisor",
    "QUALITY ANALYST": "QA / Quality",
    "ANALISTA DE CALIDAD": "QA / Quality",
    "TRAINER": "Trainer",
    "ENTRENADOR": "Trainer",
    "GERENTE DE OPERACIONES": "Operations Manager",
    "OPERATIONS MANAGER": "Operations Manager",
    "DIRECTOR DE OPERACIONES": "Site Director",
    "SITE DIRECTOR": "Site Director",
    "CLEANING STAFF": "Cleaning Staff",
    "PERSONAL DE LIMPIEZA COMERCIAL": "Cleaning Staff",
    "PERSONAL DE LIMPIEZA": "Cleaning Staff",
    "GENERALISTA TI": "IT Generalist",
    "IT GENERALIST": "IT Generalist",
    "IT ASSISTANT": "IT Desktop",
    "IT DESKTOP": "IT Desktop",
    "ACCOUNTING AND SCHEDULING ANALYST": "Payroll / Accounting",
    "GERENTE DE OFICINA": "Support Staff",
    "ASISTENTE DE OFICINA": "Support Staff",
    "CONSULTANT": "Support Staff",
    "GERENTE GENERAL": "General Manager",
    "GENERAL MANAGER": "General Manager",
    "GERENTE DE RRHH": "HR Manager",
    "HR MANAGER": "HR Manager",
    "PRESIDENTE": "President",
    "PRESIDENT": "President",
    "VICEPRESIDENTE": "Vice President",
    "VICE PRESIDENT": "Vice President",
}


# Fixes for employees whose job title does not identify their group.
# Keyed by ID_EMP so a reused title like "POR DEFINIR" cannot mislabel a new hire.
EMPLOYEE_OVERRIDES = {
    "218353": "President",
}


def find_header_row(raw, marker="INGRESO_BRUTO", limit=40):
    """Locate the row index that holds the column headings."""
    for i in range(min(limit, len(raw))):
        values = [str(v).strip().upper() for v in raw.iloc[i].tolist()]
        if marker in values:
            return i
    return 0


def read_payroll(file_like, name=""):
    """Read a DV Pre-Planilla export into a clean dataframe plus report metadata."""
    if str(name).lower().endswith(".csv"):
        raw = pd.read_csv(file_like, header=None, dtype=str)
        header = find_header_row(raw)
        file_like.seek(0)
        df = pd.read_csv(file_like, header=header)
    else:
        raw = pd.read_excel(file_like, header=None, dtype=str)
        header = find_header_row(raw)
        df = pd.read_excel(file_like, header=header)

    meta = {}
    for i in range(header):
        label = str(raw.iloc[i, 0]).strip()
        if label and label.lower() != "nan":
            row = [str(v).strip() for v in raw.iloc[i].tolist()
                   if str(v).strip() and str(v).strip().lower() != "nan"]
            if len(row) > 1:
                meta[label] = row[1]

    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df[df["ID_EMP"].notna()] if "ID_EMP" in df.columns else df

    if "ESTADO" in df.columns:
        df = df[df["ESTADO"].astype(str).str.upper().str.strip() == "ACTIVO"]

    numeric_skip = {"COMPANIA", "MES", "SUCURSAL", "COD_DEPTO", "DESC_DEPTO", "C_COSTO",
                    "DESC_COSTO", "ID_EMP", "NO_EMPLEADO", "ESTADO", "NOMBRE", "CEDULA",
                    "NO_S_S", "CARGO", "TIPO_PAGO"}
    for col in df.columns:
        if col not in numeric_skip:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if "CARGO" in df.columns:
        titles = df["CARGO"].astype(str).str.strip().str.upper()
        df["GRUPO"] = titles.map(GROUP_MAP).fillna("Other / Unmapped")
        df["CARGO_LIMPIO"] = titles
    else:
        df["GRUPO"] = "Other / Unmapped"
        df["CARGO_LIMPIO"] = ""

    if "ID_EMP" in df.columns:
        ids = df["ID_EMP"].astype(str).str.split(".").str[0].str.strip()
        df["GRUPO"] = [EMPLOYEE_OVERRIDES.get(i, g) for i, g in zip(ids, df["GRUPO"])]

    if "NOMBRE" in df.columns:
        df["NOMBRE"] = df["NOMBRE"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()

    return df, meta


def col(df, name):
    return df[name] if name in df.columns else pd.Series(0.0, index=df.index)


def summarize(df):
    """Headline figures for one payroll period."""
    loans = sum(col(df, c).sum() for c in LOAN_COLUMNS)
    employee_statutory = col(df, "DED_SS").sum() + col(df, "DED_SE").sum() \
        + col(df, "DED_ISR").sum() + col(df, "DED_ISR_GREP").sum() \
        + col(df, "DED_ISR_LIQ").sum()
    employer_cost = col(df, "TOTAL_GASTO_PAT").sum()
    return {
        "headcount": int(len(df)),
        "gross": float(col(df, "INGRESO_BRUTO").sum()),
        "net": float(col(df, "INGRESO_NETO").sum()),
        "deductions": float(col(df, "TOTAL_RETENCIONES").sum()),
        "employee_statutory": float(employee_statutory),
        "employer_cost": float(employer_cost),
        "loans": float(loans),
        "viatico": float(col(df, "VITICO").sum()),
        "decimo_paid": float(col(df, "XIII_MES").sum() + col(df, "XIII_MES_GREP").sum()),
        "decimo_accrued": float(col(df, "ACUM_XIII").sum() + col(df, "ACUM_XIII_GREP").sum()),
        "overtime": float(col(df, "MONTO_SOBRETIEMPO").sum()),
        "vacation": float(col(df, "VACACIONES").sum()),
    }


def by_group(df):
    """Payroll totals per employee group."""
    out = df.groupby("GRUPO").apply(
        lambda g: pd.Series({
            "Headcount": int(len(g)),
            "Base Salary": float(col(g, "SALARIO_MENSUAL").sum()),
            "Regular Pay": float(col(g, "INGRESO_REGULAR").sum()),
            "Overtime": float(col(g, "MONTO_SOBRETIEMPO").sum()),
            "Decimo Paid": float(col(g, "XIII_MES").sum() + col(g, "XIII_MES_GREP").sum()),
            "Vacation Paid": float(col(g, "VACACIONES").sum() + col(g, "VACACIONES_GREP").sum()),
            "Viatico": float(col(g, "VITICO").sum()),
            "Gross Pay": float(col(g, "INGRESO_BRUTO").sum()),
            "Deductions": float(col(g, "TOTAL_RETENCIONES").sum()),
            "Net Pay": float(col(g, "INGRESO_NETO").sum()),
            "Employer Cost": float(col(g, "TOTAL_GASTO_PAT").sum()),
        }),
        include_groups=False,
    ).reset_index().rename(columns={"GRUPO": "Employee Group"})
    return out.sort_values("Net Pay", ascending=False)


def loan_detail(df):
    """Pass-through loan deductions, which are not company expenses."""
    rows = []
    for c in LOAN_COLUMNS:
        total = float(col(df, c).sum())
        count = int((col(df, c) > 0).sum())
        if total:
            rows.append({"Program": c, "Employees": count, "Amount": total})
    return pd.DataFrame(rows)

# ---------------------------------------------------------------------------
# Accrued versus cash. The planilla carries both: provision columns that never
# leave the bank in the period (PROV_*, ACUM_XIII) and benefit payouts that do
# leave the bank because someone was actually paid (vacation taken, a decimo
# advance, a liquidacion).
# ---------------------------------------------------------------------------

ACCRUED_LINES = [
    ("Decimo accrued this period", ["ACUM_XIII", "ACUM_XIII_GREP"]),
    ("Vacation provision", ["PROV_VACACIONES", "PROV_VACGAS"]),
    ("Prima de antiguedad provision", ["PROV_PRIMA"]),
    ("Indemnizacion provision", ["PROV_INDEM"]),
]

BENEFIT_CASH_LINES = [
    ("Vacation paid out", ["VACACIONES", "VACACIONES_GREP"]),
    ("Decimo paid in this period", ["XIII_MES", "XIII_MES_GREP"]),
    ("Prima de antiguedad paid", ["PRIMA_LIQ"]),
    ("Preaviso paid", ["PREAVISO_LIQ"]),
    ("Indemnizacion paid", ["INDEMNIZACION_LIQ"]),
]


def _total(df, columns):
    return float(sum(col(df, c).sum() for c in columns))


PERIOD_KINDS = {
    "QUINCENAL": ("Quincenal - twice a month", 2.0),
    "MENSUAL": ("Mensual - once a month", 1.0),
    "BISEMANAL": ("Bisemanal - every two weeks", 2.1667),
    "SEMANAL": ("Semanal - weekly", 4.3333),
    "DIARIO": ("Diario", 30.0),
}


def period_kind(meta):
    """Work out how often this planilla runs, straight from the report header.

    A quincenal file covers half a month, so monthly figures are double it. A
    mensual file already covers the whole month and must not be doubled, which
    is the difference between a correct payroll line and one twice its size.
    """
    text = " ".join(str(v).upper() for v in (meta or {}).values())
    for key, (label, per_month) in PERIOD_KINDS.items():
        if key in text:
            return label, per_month
    return "Not stated in the file", 2.0


def accrual_breakdown(df, per_month=2.0):
    """One row per benefit line, split into what is cash and what is accrued."""
    rows = []
    for label, columns in BENEFIT_CASH_LINES:
        rows.append({"Line": label, "This period": _total(df, columns),
                     "Treatment": "Cash - money left the bank"})
    for label, columns in ACCRUED_LINES:
        rows.append({"Line": label, "This period": _total(df, columns),
                     "Treatment": "Accrued only - no cash yet"})
    out = pd.DataFrame(rows)
    out["Monthly"] = out["This period"] * per_month
    return out[out["This period"] != 0]


def col_isr_liq(df):
    return col(df, "DED_ISR_LIQ").sum()


def accrual_totals(df):
    """Headline accrued and benefit-cash figures for one period."""
    accrued = {label: _total(df, c) for label, c in ACCRUED_LINES}
    benefit_cash = {label: _total(df, c) for label, c in BENEFIT_CASH_LINES}
    return {
        "accrued_total": float(sum(accrued.values())),
        "benefit_cash_total": float(sum(benefit_cash.values())),
        "decimo_accrued": float(accrued["Decimo accrued this period"]),
        "vacation_accrued": float(accrued["Vacation provision"]),
        "prima_accrued": float(accrued["Prima de antiguedad provision"]),
        "indem_accrued": float(accrued["Indemnizacion provision"]),
        "vacation_cash": float(benefit_cash["Vacation paid out"]),
        "decimo_cash": float(benefit_cash["Decimo paid in this period"]),
        "isr_on_liquidacion": float(col_isr_liq(df)),
        "liquidacion_cash": float(
            benefit_cash["Prima de antiguedad paid"]
            + benefit_cash["Preaviso paid"]
            + benefit_cash["Indemnizacion paid"]),
    }

# Money withheld from employees that the company forwards to somebody else:
# cooperatives, finance companies, garnishments. It leaves the bank with the
# payroll run, but it is not part of net pay and it is not a government tax, so
# without this it goes missing from the forecast entirely.
COMPANY_RECEIVABLE_COLUMNS = ["CXCEMP"]


def third_party_deductions(df):
    """What is withheld and remitted to third parties, per period.

    Total deductions less the employee's own statutory taxes leaves the
    third-party items. Employee receivables are taken out, because that money
    comes back to the company rather than going out of it.
    """
    total_deductions = float(col(df, "TOTAL_RETENCIONES").sum())
    statutory = float(
        col(df, "DED_SS").sum() + col(df, "DED_SE").sum()
        + col(df, "DED_ISR").sum() + col(df, "DED_ISR_GREP").sum()
        + col(df, "DED_ISR_LIQ").sum())
    receivable = float(sum(col(df, c).sum() for c in COMPANY_RECEIVABLE_COLUMNS))
    remitted = total_deductions - statutory - receivable
    named = {c: float(col(df, c).sum()) for c in LOAN_COLUMNS
             if c not in COMPANY_RECEIVABLE_COLUMNS and float(col(df, c).sum())}
    identified = sum(named.values())
    detail = pd.DataFrame(
        [{"Program": k, "Amount": v} for k, v in named.items()]
        + ([{"Program": "Other withheld amounts", "Amount": remitted - identified}]
           if round(remitted - identified, 2) else [])
    )
    return {
        "remitted": max(remitted, 0.0),
        "receivable": receivable,
        "detail": detail.sort_values("Amount", ascending=False)
                  if not detail.empty else detail,
    }
