"""Payroll maths from the brief, with no database code so it is easy to read and test.

    gross      = 1.5 x basic                    (basic + 40% HRA + 10% medical)
    per day    = gross / working days of the month
    deduction  = per day x (absent + unpaid leave + 0.5 x half days)
    prorated   = gross x working days employed / working days of the month
    tax        = slab(gross x 12) / 12          (on the FULL monthly gross)
    net        = prorated - deduction - tax     (never below zero)
"""
from decimal import ROUND_HALF_UP, Decimal

HRA_RATE = Decimal("0.40")
GROSS_MULTIPLIER = Decimal("1.5")

# (from, up to, tax on the amount below "from", rate on the part above "from")
TAX_SLABS = [
    (Decimal("0"), Decimal("600000"), Decimal("0"), Decimal("0")),
    (Decimal("600000"), Decimal("1200000"), Decimal("0"), Decimal("0.05")),
    (Decimal("1200000"), Decimal("2400000"), Decimal("30000"), Decimal("0.15")),
    (Decimal("2400000"), Decimal("3600000"), Decimal("210000"), Decimal("0.25")),
    (Decimal("3600000"), None, Decimal("510000"), Decimal("0.35")),
]


def _dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value) -> Decimal:
    """Round to 2 decimals, halves go up (the way people round on paper)."""
    return _dec(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def salary_components(basic) -> dict:
    basic = money(basic)
    gross = money(basic * GROSS_MULTIPLIER)
    hra = money(basic * HRA_RATE)
    medical = gross - basic - hra  # the balancing amount, so the parts always add up
    return {"basic": basic, "hra": hra, "medical": medical, "gross": gross}


def annual_tax(annual_income) -> Decimal:
    income = _dec(annual_income)
    for lower, upper, base, rate in TAX_SLABS:
        if upper is None or income <= upper:
            return base + (income - lower) * rate
    raise AssertionError("the last slab has no upper limit")


def monthly_tax(monthly_gross) -> Decimal:
    return money(annual_tax(_dec(monthly_gross) * 12) / 12)


def calculate_pay(gross, working_days: int, payable_days: int, deduction_days) -> dict:
    gross = money(gross)
    deduction_days = _dec(deduction_days)
    prorated = money(gross * payable_days / working_days)
    deduction = money(gross * deduction_days / working_days)
    tax = monthly_tax(gross)
    net = money(max(Decimal("0"), prorated - deduction - tax))
    return {
        "per_day_rate": money(gross / working_days),
        "prorated_gross": prorated,
        "attendance_deduction": deduction,
        "tax": tax,
        "net_pay": net,
    }