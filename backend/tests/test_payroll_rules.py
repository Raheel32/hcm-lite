from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core import payroll_rules as rules
from app.models.payroll import RunStatus
from app.services.payslip_pdf import build_payslip_pdf, payslip_sections


def test_salary_components_follow_the_brief():
    parts = rules.salary_components(50000)
    assert parts["basic"] == Decimal("50000.00")
    assert parts["hra"] == Decimal("20000.00")      # 40% of basic
    assert parts["medical"] == Decimal("5000.00")   # 10% of basic
    assert parts["gross"] == Decimal("75000.00")    # 1.5 x basic


@pytest.mark.parametrize("basic", [33333.33, 41234.57, 99999.99, 12345.67, 100000])
def test_components_always_add_up_to_the_gross(basic):
    parts = rules.salary_components(basic)
    assert parts["basic"] + parts["hra"] + parts["medical"] == parts["gross"]
    assert parts["gross"] == rules.money(Decimal(str(basic)) * Decimal("1.5"))


def test_odd_salary_rounds_the_way_people_do_on_paper():
    parts = rules.salary_components(33333.33)
    assert parts["gross"] == Decimal("50000.00")   # 49999.995 rounds up
    assert parts["hra"] == Decimal("13333.33")
    assert parts["medical"] == Decimal("3333.34")  # the balancing amount


@pytest.mark.parametrize(
    "annual,expected",
    [
        (0, "0"),
        (600000, "0"),
        (600001, "0.05"),
        (900000, "15000"),
        (1200000, "30000"),
        (1800000, "120000"),
        (2400000, "210000"),
        (3000000, "360000"),
        (3600000, "510000"),
        (4000000, "650000"),
    ],
)
def test_tax_slab_on_the_annual_income(annual, expected):
    assert rules.annual_tax(annual) == Decimal(expected)


@pytest.mark.parametrize(
    "monthly_gross,expected",
    [
        (49999, "0.00"),       # 599,988 a year: still in the tax-free slab
        (50000, "0.00"),       # exactly 600,000 a year
        (60000, "500.00"),     # 720,000 a year: 5% of the 120,000 above 600,000, over 12 months
        (75000, "1250.00"),    # 900,000 a year
        (150000, "10000.00"),  # 1,800,000 a year: 30,000 + 15% of 600,000, over 12 months
    ],
)
def test_monthly_tax(monthly_gross, expected):
    assert rules.monthly_tax(monthly_gross) == Decimal(expected)


def test_a_full_month():
    pay = rules.calculate_pay(75000, working_days=20, payable_days=20, deduction_days=0)
    assert pay["per_day_rate"] == Decimal("3750.00")
    assert pay["prorated_gross"] == Decimal("75000.00")
    assert pay["attendance_deduction"] == Decimal("0.00")
    assert pay["tax"] == Decimal("1250.00")
    assert pay["net_pay"] == Decimal("73750.00")


def test_deductions():
    pay = rules.calculate_pay(75000, working_days=20, payable_days=20, deduction_days=5)
    assert pay["attendance_deduction"] == Decimal("18750.00")  # 5 days x 3,750
    assert pay["net_pay"] == Decimal("55000.00")


def test_half_days_are_half_a_day():
    pay = rules.calculate_pay(75000, working_days=20, payable_days=20, deduction_days=2.5)
    assert pay["attendance_deduction"] == Decimal("9375.00")


def test_a_joiner_is_paid_for_the_days_employed():
    pay = rules.calculate_pay(75000, working_days=20, payable_days=13, deduction_days=0)
    assert pay["prorated_gross"] == Decimal("48750.00")
    assert pay["tax"] == Decimal("1250.00")  # tax is worked out on the full monthly gross
    assert pay["net_pay"] == Decimal("47500.00")


def test_amounts_are_rounded_only_at_the_end():
    pay = rules.calculate_pay(75000, working_days=22, payable_days=22, deduction_days=2)
    assert pay["per_day_rate"] == Decimal("3409.09")
    assert pay["attendance_deduction"] == Decimal("6818.18")  # 150,000 / 22, not 2 x 3,409.09
    assert pay["net_pay"] == Decimal("66931.82")


def test_net_pay_never_goes_below_zero():
    pay = rules.calculate_pay(75000, working_days=20, payable_days=20, deduction_days=20)
    assert pay["attendance_deduction"] == Decimal("75000.00")
    assert pay["net_pay"] == Decimal("0.00")


# ---------- payslip text and PDF ----------

def fake_slip(status=RunStatus.finalized):
    return SimpleNamespace(
        run=SimpleNamespace(year=2026, month=2, status=status),
        full_name="Ali Khan", employee_code="EMP-0001",
        department_name="Engineering", designation_name="Developer",
        basic=50000.0, hra=20000.0, medical=5000.0, gross_salary=75000.0,
        working_days=20, payable_days=20, absent_days=2, unpaid_days=1, half_days=2, deduction_days=4.0,
        per_day_rate=3750.0, prorated_gross=75000.0, attendance_deduction=15000.0, tax=1250.0, net_pay=58750.0,
    )


def test_payslip_text():
    sections = dict(payslip_sections(fake_slip()))
    assert dict(sections["Employee"])["Employee code"] == "EMP-0001"
    assert dict(sections["Salary structure (monthly)"])["Gross salary"] == "PKR 75,000.00"
    assert dict(sections["Attendance"])["Deduction days"] == "4"
    assert dict(sections["Pay"])["Income tax"] == "PKR 1,250.00"
    assert dict(sections["Pay"])["Net pay"] == "PKR 58,750.00"


@pytest.mark.parametrize("status", [RunStatus.finalized, RunStatus.draft])
def test_the_pdf_is_a_real_pdf(status):
    pdf = build_payslip_pdf(fake_slip(status), "Test Co")
    assert pdf.startswith(b"%PDF")
    assert b"%%EOF" in pdf[-20:]
    assert len(pdf) > 1000