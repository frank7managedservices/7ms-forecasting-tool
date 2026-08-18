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
