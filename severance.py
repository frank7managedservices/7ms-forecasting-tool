"""Panama liquidacion (severance) estimates and upcoming payment tracking.

Rules applied, per the Codigo de Trabajo de Panama:

- Weekly salary = monthly salary x 12 / 52.
- Daily salary  = monthly salary / 30.
- Prima de antiguedad (Art. 224): 1 week of salary per year of service, paid on
  termination of an indefinite contract regardless of cause. Partial years are
  paid proportionally.
- Indemnizacion (Art. 225): 3.4 weeks of salary per year for the first 10 years,
  then 1 week per year from year 11. Only for despido injustificado.
- Decimo tercer mes proporcional: accrued since the last decimo payment
  (15 April, 15 August, 15 December).
- Vacaciones proporcionales: 30 days of vacation per 11 months worked.
- Payment is scheduled 30 days after the termination date.

These are estimates for cash planning. The final figure comes from payroll.
"""

import json
from datetime import date, timedelta
from pathlib import Path

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
    if TERMINATIONS_FILE.exists():
        try:
            return json.loads(TERMINATIONS_FILE.read_text())
        except Exception:
            return []
    return []


def save_terminations(records):
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
