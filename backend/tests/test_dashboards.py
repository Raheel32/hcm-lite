from datetime import date, datetime, timedelta, timezone

import pytest

from app.core import clock
from app.core.security import create_access_token
from app.models.attendance import Attendance, DayStatus
from app.models.comms import Announcement, Notification
from app.models.company import Company
from app.models.correction import AttendanceCorrection, CorrectionStatus
from app.models.employee import Employee, EmployeeStatus, Gender
from app.models.leave import LeaveRequest, LeaveStatus
from app.models.leave_config import Holiday, LeaveType
from app.models.org import Department, Designation
from app.models.payroll import PayrollRun, Payslip, RunStatus
from app.models.user import Role

# "Now" is Thursday 2026-11-05, 10:00 office time (UTC+5).


def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value)}"}


def set_now(monkeypatch, text):
    moment = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=clock.office_tz())
    monkeypatch.setattr(clock, "now", lambda: moment)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    set_now(monkeypatch, "2026-11-05 10:00")


@pytest.fixture()
def company(db_session):
    db_session.add(Company(name="Test Co"))
    db_session.commit()


@pytest.fixture()
def org(db_session, company):
    rows = {
        "eng": Department(name="Engineering"),
        "sales": Department(name="Sales"),
        "dev": Designation(name="Developer"),
        "mgr": Designation(name="Manager"),
    }
    db_session.add_all(rows.values())
    db_session.commit()
    return {key: row.id for key, row in rows.items()}


@pytest.fixture()
def types(db_session, company):
    rows = {
        "Annual": LeaveType(name="Annual", yearly_quota=14, is_paid=True),
        "Sick": LeaveType(name="Sick", yearly_quota=8, is_paid=True),
        "Unpaid": LeaveType(name="Unpaid", yearly_quota=None, is_paid=False),
    }
    db_session.add_all(rows.values())
    db_session.commit()
    return rows


@pytest.fixture()
def make_employee(db_session, make_user, org):
    counter = {"n": 0}

    def _make(name, dept="eng", desig="dev", join=date(2025, 1, 1), exit_date=None, basic=50000, gender=None):
        counter["n"] += 1
        user = make_user(email=f"{name.lower().replace(' ', '')}@t.com", role=Role.employee)
        emp = Employee(
            user_id=user.id, employee_code=f"EMP-{counter['n']:04d}", full_name=name, gender=gender,
            department_id=org[dept], designation_id=org[desig], join_date=join, exit_date=exit_date,
            status=EmployeeStatus.exited if exit_date else EmployeeStatus.active, basic_salary=basic,
        )
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        return user, emp

    return _make


@pytest.fixture()
def hr_user(make_user):
    return make_user(email="hr@t.com", role=Role.hr)


@pytest.fixture()
def hr(hr_user):
    return auth(hr_user)


@pytest.fixture()
def admin(make_user):
    return auth(make_user(email="admin@t.com", role=Role.admin))


def utc(day, hour):
    """An office-local hour on a day, as the UTC time that gets stored."""
    return datetime(day.year, day.month, day.day, hour, tzinfo=clock.office_tz()).astimezone(timezone.utc)


def add_record(db, emp, day, status=DayStatus.present, is_late=False, closed=True, in_hour=9, out_hour=17):
    db.add(Attendance(
        employee_id=emp.id, date=day, check_in=utc(day, in_hour),
        check_out=utc(day, out_hour) if closed else None,
        status=status, is_late=is_late, source="web",
    ))
    db.commit()


def add_leave(db, emp, leave_type, start, end, days, status=LeaveStatus.approved):
    db.add(LeaveRequest(
        employee_id=emp.id, leave_type_id=leave_type.id, start_date=start, end_date=end,
        days=days, reason="Test leave", status=status,
    ))
    db.commit()


def add_run(db, year, month, status, created_by_id):
    run = PayrollRun(
        year=year, month=month, status=status, working_days=22, as_of_date=date(2026, 11, 5),
        created_by_id=created_by_id, calculated_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def add_payslip(db, run, emp, gross, net, tax):
    slip = Payslip(
        run_id=run.id, employee_id=emp.id, employee_code=emp.employee_code, full_name=emp.full_name,
        department_id=emp.department_id, department_name=emp.department.name,
        designation_name=emp.designation.name, basic=0.0, hra=0.0, medical=0.0, gross_salary=gross,
        working_days=22, payable_days=22, absent_days=0, unpaid_days=0, half_days=0, deduction_days=0,
        per_day_rate=0.0, prorated_gross=gross, attendance_deduction=0.0, tax=tax, net_pay=net,
    )
    db.add(slip)
    db.commit()
    db.refresh(slip)
    return slip


@pytest.fixture()
def scenario(db_session, make_employee, types):
    """Ann and Bob in Engineering, Cid in Sales, working Mon 2 to Thu 5 November."""
    ann_user, ann = make_employee("Ann", dept="eng")
    bob_user, bob = make_employee("Bob", dept="eng")
    cid_user, cid = make_employee("Cid", dept="sales")

    add_record(db_session, ann, date(2026, 11, 2), DayStatus.present)
    add_record(db_session, ann, date(2026, 11, 3), DayStatus.late, is_late=True)
    add_record(db_session, ann, date(2026, 11, 4), DayStatus.half_day)
    add_record(db_session, ann, date(2026, 11, 5), DayStatus.late, is_late=True)   # today
    add_record(db_session, bob, date(2026, 11, 2), DayStatus.present)
    # Bob has no record on the 3rd (absent), forgot to check out on the 4th, and has not come in today
    add_record(db_session, bob, date(2026, 11, 4), DayStatus.present, closed=False)
    add_leave(db_session, cid, types["Annual"], date(2026, 11, 2), date(2026, 11, 4), 3)   # 2 to 4 on leave
    add_record(db_session, cid, date(2026, 11, 5), DayStatus.late, is_late=True)           # back today, late

    add_leave(db_session, ann, types["Sick"], date(2026, 11, 9), date(2026, 11, 9), 1)     # approved, later this month
    add_leave(db_session, ann, types["Annual"], date(2026, 11, 16), date(2026, 11, 17), 2, LeaveStatus.pending)
    db_session.add(AttendanceCorrection(
        employee_id=cid.id, date=date(2026, 11, 3), reason="Forgot to check in", status=CorrectionStatus.pending,
    ))
    db_session.commit()
    return {"ann": ann, "bob": bob, "cid": cid, "bob_user": bob_user, "ann_user": ann_user}


def get(client, headers, path, query=""):
    return client.get(f"{path}{query}", headers=headers)


# ---------- HR dashboard: attendance numbers ----------

def test_hr_dashboard_numbers(client, hr, org, types, scenario):
    r = get(client, hr, "/dashboard/hr", "?date_from=2026-11-02&date_to=2026-11-05")
    assert r.status_code == 200
    body = r.json()
    ann, cid = scenario["ann"], scenario["cid"]

    assert body["range"] == {"date_from": "2026-11-02", "date_to": "2026-11-05", "department_id": None}

    # Today: Ann and Cid are in (both late), Bob has not come
    assert body["kpis"] == {
        "date": "2026-11-05", "headcount": 3, "present": 2, "late_today": 2, "on_leave": 0,
        "not_checked_in": 1, "pending_approvals": {"leave": 1, "corrections": 1, "total": 2},
    }

    period = body["period"]
    assert period["counts"] == {
        "present": 2, "late": 3, "half_day": 1, "missing_checkout": 1, "absent": 1, "on_leave": 3, "late_arrivals": 3,
    }
    # attended = 2 + 3 + 1 + 1 = 7, absent = 1, on leave = 3
    assert period["attendance_rate"] == 81.25        # (2 + 3 + 1 + 0.5) / (7 + 1)
    assert period["late_percent"] == 42.86           # 3 / 7
    assert period["absenteeism_percent"] == 9.09     # 1 / (7 + 1 + 3)
    assert period["expected_working_days"] == 11

    assert body["attendance_trend"] == [
        {"date": "2026-11-02", "present": 2, "late": 0, "half_day": 0, "missing_checkout": 0, "absent": 0, "on_leave": 1, "attendance_rate": 100.0},
        {"date": "2026-11-03", "present": 0, "late": 1, "half_day": 0, "missing_checkout": 0, "absent": 1, "on_leave": 1, "attendance_rate": 50.0},
        {"date": "2026-11-04", "present": 0, "late": 0, "half_day": 1, "missing_checkout": 1, "absent": 0, "on_leave": 1, "attendance_rate": 75.0},
        {"date": "2026-11-05", "present": 0, "late": 2, "half_day": 0, "missing_checkout": 0, "absent": 0, "on_leave": 0, "attendance_rate": 100.0},
    ]

    assert body["absenteeism_by_department"] == [
        {"department_id": org["eng"], "department": "Engineering", "absent_days": 1,
         "expected_working_days": 7, "absenteeism_percent": 14.29, "attendance_rate": 78.57},
        {"department_id": org["sales"], "department": "Sales", "absent_days": 0,
         "expected_working_days": 4, "absenteeism_percent": 0.0, "attendance_rate": 100.0},
    ]
    assert body["top_late_comers"] == [
        {"employee_id": ann.id, "employee_code": "EMP-0001", "full_name": "Ann", "department": "Engineering", "late_count": 2},
        {"employee_id": cid.id, "employee_code": "EMP-0003", "full_name": "Cid", "department": "Sales", "late_count": 1},
    ]

    # Leave that starts inside the range: Cid's 3 days. Ann's sick day (the 9th) and the pending request do not count.
    assert body["leave_by_type"] == [
        {"leave_type_id": types["Annual"].id, "leave_type": "Annual", "days": 3, "requests": 1}
    ]
    assert body["leave_year"] == 2026
    rows = {(r["leave_type"], r["department"]): r for r in body["leave_utilization"]}
    assert len(rows) == 6  # 3 leave types x 2 departments
    assert rows[("Annual", "Engineering")] | {} == rows[("Annual", "Engineering")]
    for key, (approved, quota_total, percent) in {
        ("Annual", "Engineering"): (0, 28, 0.0),
        ("Annual", "Sales"): (3, 14, 21.43),
        ("Sick", "Engineering"): (1, 16, 6.25),
        ("Sick", "Sales"): (0, 8, 0.0),
        ("Unpaid", "Engineering"): (0, None, None),   # unpaid leave has no quota
        ("Unpaid", "Sales"): (0, None, None),
    }.items():
        assert (rows[key]["approved_days"], rows[key]["quota_total"], rows[key]["utilization_percent"]) == (approved, quota_total, percent)
    assert rows[("Annual", "Engineering")]["headcount"] == 2
    assert rows[("Annual", "Engineering")]["yearly_quota"] == 14


def test_hr_dashboard_department_filter(client, hr, org, scenario):
    r = get(client, hr, "/dashboard/hr", f"?date_from=2026-11-02&date_to=2026-11-05&department_id={org['sales']}")
    body = r.json()
    assert body["range"]["department_id"] == org["sales"]
    assert body["kpis"] == {
        "date": "2026-11-05", "headcount": 1, "present": 1, "late_today": 1, "on_leave": 0,
        "not_checked_in": 0, "pending_approvals": {"leave": 0, "corrections": 1, "total": 1},
    }
    assert body["period"]["counts"] == {
        "present": 0, "late": 1, "half_day": 0, "missing_checkout": 0, "absent": 0, "on_leave": 3, "late_arrivals": 1,
    }
    # With nobody absent or present on the three leave days there is no rate to show
    assert [p["attendance_rate"] for p in body["attendance_trend"]] == [None, None, None, 100.0]
    assert [d["department"] for d in body["absenteeism_by_department"]] == ["Sales"]
    assert [c["full_name"] for c in body["top_late_comers"]] == ["Cid"]
    assert [row["department"] for row in body["leave_utilization"]] == ["Sales", "Sales", "Sales"]

    eng = get(client, hr, "/dashboard/hr", f"?department_id={org['eng']}").json()
    assert eng["kpis"]["pending_approvals"] == {"leave": 1, "corrections": 0, "total": 1}


def test_numbers_follow_the_data(client, hr, scenario):
    """Nothing is stored or hardcoded: when Bob checks in, the numbers move."""
    query = "?date_from=2026-11-02&date_to=2026-11-05"
    assert get(client, hr, "/dashboard/hr", query).json()["kpis"]["not_checked_in"] == 1

    assert client.post("/attendance/check-in", headers=auth(scenario["bob_user"])).status_code == 201  # 10:00, so late

    body = get(client, hr, "/dashboard/hr", query).json()
    assert (body["kpis"]["present"], body["kpis"]["late_today"], body["kpis"]["not_checked_in"]) == (3, 3, 0)
    assert body["period"]["counts"]["late"] == 4
    assert body["period"]["counts"]["late_arrivals"] == 4
    assert body["period"]["attendance_rate"] == 83.33  # (2 + 4 + 1 + 0.5) / (8 + 1)
    assert [c["full_name"] for c in body["top_late_comers"]] == ["Ann", "Bob", "Cid"]  # ties are sorted by name


def test_dashboards_agree_with_the_board_and_payroll(client, hr, scenario):
    board = client.get("/attendance/board?date=2026-11-05", headers=hr).json()
    kpis = get(client, hr, "/dashboard/hr").json()["kpis"]
    assert board["summary"] == {"late": 2, "not_checked_in": 1}
    assert (kpis["present"], kpis["late_today"], kpis["not_checked_in"]) == (2, 2, 1)

    run = client.post("/payroll/runs", json={"year": 2026, "month": 11}, headers=hr).json()
    slips = client.get(f"/payroll/runs/{run['id']}/payslips", headers=hr).json()["items"]
    period = get(client, hr, "/dashboard/hr", "?date_from=2026-11-01&date_to=2026-11-30").json()["period"]
    assert sum(s["absent_days"] for s in slips) == period["counts"]["absent"] == 1
    assert sum(s["half_days"] for s in slips) == period["counts"]["half_day"] == 1


# ---------- HR dashboard: edge cases ----------

def test_weekends_holidays_and_weekend_check_ins_are_not_counted(client, hr, db_session, make_employee):
    _, ann = make_employee("Ann")
    db_session.add(Holiday(name="Special Day", date=date(2026, 10, 28)))
    db_session.commit()
    add_record(db_session, ann, date(2026, 10, 31), DayStatus.present)  # a Saturday

    body = get(client, hr, "/dashboard/hr", "?date_from=2026-10-26&date_to=2026-11-01").json()
    period = body["period"]
    assert period["counts"]["absent"] == 4         # Mon 26, Tue 27, Thu 29, Fri 30
    assert period["counts"]["present"] == 0        # the Saturday check-in is ignored
    assert period["expected_working_days"] == 4
    assert period["absenteeism_percent"] == 100.0
    assert period["attendance_rate"] == 0.0        # zero is a real rate, not a missing one
    assert [p["date"] for p in body["attendance_trend"]] == ["2026-10-26", "2026-10-27", "2026-10-29", "2026-10-30"]


def test_people_only_count_inside_their_employment(client, hr, make_employee):
    make_employee("New Joiner", join=date(2026, 11, 3))
    make_employee("Leaver", exit_date=date(2026, 11, 3))
    body = get(client, hr, "/dashboard/hr", "?date_from=2026-11-02&date_to=2026-11-04").json()
    assert body["period"]["counts"]["absent"] == 4     # joiner: 3rd and 4th. Leaver: 2nd and 3rd
    assert body["period"]["expected_working_days"] == 4
    assert body["kpis"]["headcount"] == 1               # the leaver is gone today


def test_an_empty_company_gives_nulls_not_errors(client, hr, admin, company):
    body = get(client, hr, "/dashboard/hr").json()
    assert body["kpis"]["headcount"] == 0
    assert body["period"]["counts"] == {
        "present": 0, "late": 0, "half_day": 0, "missing_checkout": 0, "absent": 0, "on_leave": 0, "late_arrivals": 0,
    }
    assert body["period"]["attendance_rate"] is None
    assert body["period"]["late_percent"] is None
    assert body["period"]["absenteeism_percent"] is None
    for key in ("attendance_trend", "absenteeism_by_department", "top_late_comers", "leave_by_type", "leave_utilization"):
        assert body[key] == []

    admin_body = get(client, admin, "/dashboard/admin").json()
    assert admin_body["kpis"]["headcount"] == 0
    assert admin_body["kpis"]["attrition_ytd"] == {"exits": 0, "average_headcount": 0.0, "percent": None}
    assert admin_body["kpis"]["average_salary"] == {"gross": None, "employees": 0}
    assert admin_body["kpis"]["payroll_cost"]["gross"] == 0.0
    assert all(g["count"] == 0 for g in admin_body["gender"])
    assert all(t["count"] == 0 for t in admin_body["tenure"])


# ---------- Admin dashboard ----------

@pytest.fixture()
def company_data(db_session, make_employee, hr_user):
    """Five people, two departments, and payroll history."""
    ann_user, ann = make_employee("Ann", "eng", "dev", date(2026, 6, 1), basic=50000, gender=Gender.female)
    _, bob = make_employee("Bob", "eng", "mgr", date(2024, 1, 15), basic=100000, gender=Gender.male)
    _, cid = make_employee("Cid", "sales", "dev", date(2020, 1, 1), basic=80000)
    _, dan = make_employee("Dan", "sales", "mgr", date(2023, 5, 1), exit_date=date(2026, 3, 31), basic=70000, gender=Gender.male)
    _, eve = make_employee("Eve", "eng", "dev", date(2025, 2, 1), basic=60000, gender=Gender.female)

    sep = add_run(db_session, 2026, 9, RunStatus.finalized, hr_user.id)
    add_payslip(db_session, sep, ann, 75000, 70000, 1250)
    octo = add_run(db_session, 2026, 10, RunStatus.finalized, hr_user.id)
    add_payslip(db_session, octo, ann, 75000, 70000, 1250)
    add_payslip(db_session, octo, bob, 150000, 130000, 10000)
    add_payslip(db_session, octo, cid, 120000, 100000, 6000)
    nov = add_run(db_session, 2026, 11, RunStatus.draft, hr_user.id)
    add_payslip(db_session, nov, ann, 75000, 70000, 1250)
    return {"ann": ann, "bob": bob, "cid": cid, "dan": dan, "eve": eve}


def test_admin_dashboard_numbers(client, admin, hr, org, company_data):
    assert client.post("/departments", json={"name": "Finance"}, headers=admin).status_code == 201
    assert client.post("/announcements", json={"title": "Town hall", "body": "Friday at 3 PM"}, headers=hr).status_code == 201

    body = get(client, admin, "/dashboard/admin", "?date_from=2026-09-01&date_to=2026-11-05").json()
    assert body["range"] == {"date_from": "2026-09-01", "date_to": "2026-11-05", "department_id": None}

    kpis = body["kpis"]
    assert kpis["headcount"] == 4                       # Dan left in March
    assert kpis["attrition_ytd"] == {"exits": 1, "average_headcount": 4.0, "percent": 25.0}  # 4 on 1 Jan, 4 today
    assert kpis["average_salary"] == {"gross": 108750.0, "employees": 4}                    # 1.5 x 72,500
    assert kpis["payroll_cost"] == {
        "gross": 420000.0, "net": 370000.0, "tax": 18500.0,
        "payslips": 4, "finalized_runs": 2, "draft_runs_excluded": 1,                      # November is still a draft
    }

    assert body["headcount_by_department"] == [
        {"department_id": org["eng"], "department": "Engineering", "headcount": 3},
        {"department_id": org["sales"], "department": "Sales", "headcount": 1},
    ]  # Finance has nobody, so it is left out
    assert body["payroll_by_month"] == [
        {"period": "2026-09", "gross": 75000.0, "net": 70000.0, "employees": 1},
        {"period": "2026-10", "gross": 345000.0, "net": 300000.0, "employees": 3},
    ]
    assert body["payroll_by_department"] == [
        {"department_id": org["eng"], "department": "Engineering", "gross": 300000.0, "net": 270000.0},
        {"department_id": org["sales"], "department": "Sales", "gross": 120000.0, "net": 100000.0},
    ]
    assert body["average_salary_by_department"] == [
        {"department_id": org["eng"], "department": "Engineering", "average_gross": 105000.0, "employees": 3},
        {"department_id": org["sales"], "department": "Sales", "average_gross": 120000.0, "employees": 1},
    ]
    assert body["average_salary_by_designation"] == [
        {"designation_id": org["dev"], "designation": "Developer", "average_gross": 95000.0, "employees": 3},
        {"designation_id": org["mgr"], "designation": "Manager", "average_gross": 150000.0, "employees": 1},
    ]
    assert body["gender"] == [
        {"gender": "male", "count": 1}, {"gender": "female", "count": 2},
        {"gender": "other", "count": 0}, {"gender": "unspecified", "count": 1},
    ]
    assert body["tenure"] == [
        {"bucket": "Under 1 year", "count": 1},   # Ann, since June
        {"bucket": "1-3 years", "count": 2},      # Bob and Eve
        {"bucket": "3-5 years", "count": 0},
        {"bucket": "5+ years", "count": 1},       # Cid
    ]
    assert [a["action"] for a in body["audit_feed"]] == ["announcement.create", "department.create"]
    assert [a["title"] for a in body["announcements"]] == ["Town hall"]


def test_admin_dashboard_department_filter(client, admin, org, company_data):
    body = get(client, admin, "/dashboard/admin", f"?date_from=2026-09-01&date_to=2026-11-05&department_id={org['sales']}").json()
    kpis = body["kpis"]
    assert kpis["headcount"] == 1
    assert kpis["attrition_ytd"] == {"exits": 1, "average_headcount": 1.5, "percent": 66.67}  # 2 on 1 Jan, 1 today
    assert kpis["average_salary"] == {"gross": 120000.0, "employees": 1}
    assert kpis["payroll_cost"] == {
        "gross": 120000.0, "net": 100000.0, "tax": 6000.0,
        "payslips": 1, "finalized_runs": 1, "draft_runs_excluded": 0,
    }
    assert [d["department"] for d in body["headcount_by_department"]] == ["Sales"]
    assert body["payroll_by_month"] == [{"period": "2026-10", "gross": 120000.0, "net": 100000.0, "employees": 1}]
    assert [g["count"] for g in body["gender"]] == [0, 0, 0, 1]
    assert [t["count"] for t in body["tenure"]] == [0, 0, 0, 1]


def test_default_range_is_this_month_and_draft_payroll_is_left_out(client, admin, company_data):
    body = get(client, admin, "/dashboard/admin").json()
    assert body["range"] == {"date_from": "2026-11-01", "date_to": "2026-11-05", "department_id": None}
    assert body["kpis"]["payroll_cost"] == {
        "gross": 0.0, "net": 0.0, "tax": 0.0, "payslips": 0, "finalized_runs": 0, "draft_runs_excluded": 1,
    }


def test_range_rules(client, admin, hr, company):
    def code(query, headers=admin, path="/dashboard/admin"):
        return get(client, headers, path, query).status_code

    assert get(client, admin, "/dashboard/admin", "?date_from=2026-11-03").json()["range"]["date_to"] == "2026-11-05"
    assert get(client, hr, "/dashboard/hr", "?date_to=2026-10-20").json()["range"]["date_from"] == "2026-10-01"

    for path, headers in (("/dashboard/admin", admin), ("/dashboard/hr", hr)):
        assert code("?date_from=2025-11-05&date_to=2026-11-05", headers, path) == 200   # exactly 366 days
        assert code("?date_from=2025-11-04&date_to=2026-11-05", headers, path) == 400   # 367 days
        assert code("?date_from=2026-11-04&date_to=2026-11-02", headers, path) == 400
        assert code("?date_from=2026-11-06", headers, path) == 400                      # after today
        assert code("?date_from=1999-12-31&date_to=2000-01-02", headers, path) == 400
        assert code("?date_to=2101-01-01&date_from=2100-12-01", headers, path) == 400
        assert code("?date_from=garbage", headers, path) == 422
        assert code("?department_id=abc", headers, path) == 422
        assert code("?department_id=999", headers, path) == 404


def test_the_hr_dashboard_needs_first_run_setup(client, hr):
    assert get(client, hr, "/dashboard/hr").status_code == 409


# ---------- Employee dashboard ----------

def test_employee_dashboard(client, hr, hr_user, db_session, make_employee, make_user, types):
    user, ann = make_employee("Ann")
    add_record(db_session, ann, date(2026, 11, 1), DayStatus.late, is_late=True)              # a Sunday: never counts
    add_record(db_session, ann, date(2026, 11, 2), DayStatus.present)                         # 8 hours
    add_record(db_session, ann, date(2026, 11, 3), DayStatus.half_day, is_late=True, out_hour=13)  # 4 hours, and late
    add_record(db_session, ann, date(2026, 11, 4), DayStatus.present, closed=False)           # no check-out: unknown hours
    add_record(db_session, ann, date(2026, 11, 5), DayStatus.present, closed=False)           # in since 09:00, now 10:00
    add_leave(db_session, ann, types["Annual"], date(2026, 11, 9), date(2026, 11, 10), 2)

    sep = add_run(db_session, 2026, 9, RunStatus.finalized, hr_user.id)
    add_payslip(db_session, sep, ann, 75000, 68000, 1250)
    octo = add_run(db_session, 2026, 10, RunStatus.finalized, hr_user.id)
    last = add_payslip(db_session, octo, ann, 75000, 73750, 1250)
    nov = add_run(db_session, 2026, 11, RunStatus.draft, hr_user.id)
    add_payslip(db_session, nov, ann, 75000, 70000, 1250)   # a draft is not "the last payslip"

    db_session.add(Announcement(title="Old news", body="Expired.", expires_on=date(2026, 11, 4), created_by_id=hr_user.id))
    db_session.add_all([
        Notification(user_id=user.id, kind="x", title="One", body="Unread"),
        Notification(user_id=user.id, kind="x", title="Two", body="Unread"),
        Notification(user_id=user.id, kind="x", title="Three", body="Read", read_at=datetime.now(timezone.utc)),
    ])
    db_session.commit()
    assert client.post("/announcements", json={"title": "Town hall", "body": "Friday at 3 PM"}, headers=hr).status_code == 201

    r = get(client, auth(user), "/dashboard/me")
    assert r.status_code == 200
    body = r.json()
    assert body["employee"] == {"id": ann.id, "employee_code": "EMP-0001", "full_name": "Ann"}
    assert body["today"]["status"] == "present"
    assert body["today"]["check_in"].startswith("2026-11-05T09:00:00")
    assert body["today"]["check_out"] is None
    assert body["week"] == {
        "week_start": "2026-11-02", "week_end": "2026-11-08",
        "worked_hours": 13.0, "target_hours": 40.0, "percent": 32.5,   # 8 + 4 + 0 + 1 hours of 40
    }
    assert body["month"] == "2026-11"
    assert body["late_count_month"] == 1                                # the 3rd only: the late Sunday is not a working day
    balance = {i["leave_type"]: (i["used"], i["remaining"]) for i in body["leave_balance"]["items"]}
    assert body["leave_balance"]["year"] == 2026
    assert balance == {"Annual": (2, 12), "Sick": (0, 8), "Unpaid": (0, None)}
    assert body["last_payslip"] == {"id": last.id, "period": "2026-10", "net_pay": 73750.0, "gross_pay": 75000.0}
    assert [a["title"] for a in body["announcements"]] == ["Town hall"]
    assert body["unread_notifications"] == 2


def test_a_new_employee_with_no_history(client, make_employee, types):
    user, _ = make_employee("New Joiner", join=date(2026, 12, 1))  # has not started yet
    body = get(client, auth(user), "/dashboard/me").json()
    assert body["today"] is None
    assert body["week"]["worked_hours"] == 0.0
    assert body["week"]["percent"] == 0.0
    assert body["late_count_month"] == 0
    assert body["last_payslip"] is None
    assert body["announcements"] == []
    assert body["unread_notifications"] == 0
    assert len(body["leave_balance"]["items"]) == 3


# ---------- Who can open what ----------

def test_permissions(client, admin, hr, make_employee, make_user, company):
    user, _ = make_employee("Ann")
    employee = auth(user)
    no_profile = auth(make_user(email="x@t.com", role=Role.employee))  # a login with no employee record

    assert get(client, admin, "/dashboard/admin").status_code == 200
    assert get(client, hr, "/dashboard/admin").status_code == 403
    assert get(client, employee, "/dashboard/admin").status_code == 403
    assert client.get("/dashboard/admin").status_code == 401

    assert get(client, hr, "/dashboard/hr").status_code == 200
    assert get(client, admin, "/dashboard/hr").status_code == 200
    assert get(client, employee, "/dashboard/hr").status_code == 403
    assert client.get("/dashboard/hr").status_code == 401

    assert get(client, employee, "/dashboard/me").status_code == 200
    assert get(client, hr, "/dashboard/me").status_code == 403
    assert get(client, admin, "/dashboard/me").status_code == 403
    assert get(client, no_profile, "/dashboard/me").status_code == 404
    assert client.get("/dashboard/me").status_code == 401