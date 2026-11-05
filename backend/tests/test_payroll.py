from datetime import date, datetime, timedelta, timezone

import pytest

from app.core import clock
from app.core.security import create_access_token
from app.models.attendance import Attendance, DayStatus
from app.models.company import Company
from app.models.employee import Employee, EmployeeStatus
from app.models.leave import LeaveRequest, LeaveStatus
from app.models.leave_config import Holiday, LeaveType
from app.models.org import Department, Designation
from app.models.user import Role
from app.services import settings_service

# "Now" is Thursday 2026-11-05, 10:00 office time.
# February 2026: Sundays 1, 8, 15, 22 and Saturdays 7, 14, 21, 28, so 20 working days (Mon 2 to Fri 27).
# For basic 50,000: gross 75,000, 3,750 a day, tax 1,250 a month, net 73,750 with perfect attendance.

FEB = (date(2026, 2, 2), date(2026, 2, 27))


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
    dept = Department(name="Engineering")
    desig = Designation(name="Developer")
    db_session.add_all([dept, desig])
    db_session.commit()
    return {"dept": dept.id, "desig": desig.id}


@pytest.fixture()
def types(db_session, company):
    paid = LeaveType(name="Annual", yearly_quota=14, is_paid=True)
    unpaid = LeaveType(name="Unpaid", yearly_quota=None, is_paid=False)
    db_session.add_all([paid, unpaid])
    db_session.commit()
    return {"paid": paid, "unpaid": unpaid}


@pytest.fixture()
def make_employee(db_session, make_user, org):
    counter = {"n": 0}

    def _make(email="emp@t.com", name="Ali Khan", join=date(2025, 1, 1), exit_date=None,
              basic=50000, department_id=None):
        counter["n"] += 1
        user = make_user(email=email, role=Role.employee)
        emp = Employee(
            user_id=user.id,
            employee_code=f"EMP-{counter['n']:04d}",
            full_name=name,
            department_id=department_id or org["dept"],
            designation_id=org["desig"],
            join_date=join,
            exit_date=exit_date,
            status=EmployeeStatus.exited if exit_date else EmployeeStatus.active,
            basic_salary=basic,
        )
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        return user, emp

    return _make


@pytest.fixture()
def hr(make_user):
    return auth(make_user(email="hr@t.com", role=Role.hr))


@pytest.fixture()
def admin(make_user):
    return auth(make_user(email="admin@t.com", role=Role.admin))


def add_record(db, emp, day, status=DayStatus.present, closed=True):
    db.add(Attendance(
        employee_id=emp.id,
        date=day,
        check_in=datetime(day.year, day.month, day.day, 4, 0, tzinfo=timezone.utc),   # 09:00 office time
        check_out=datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc) if closed else None,
        status=status,
        is_late=False,
        source="web",
    ))
    db.commit()


def attend(db, emp, start, end, skip=()):
    """A present record on every weekday from start to end, except the days in skip."""
    day = start
    while day <= end:
        if day.weekday() < 5 and day not in skip:
            add_record(db, emp, day)
        day += timedelta(days=1)


def add_leave(db, emp, leave_type, start, end, days, status=LeaveStatus.approved):
    db.add(LeaveRequest(
        employee_id=emp.id, leave_type_id=leave_type.id, start_date=start, end_date=end,
        days=days, reason="Test leave", status=status,
    ))
    db.commit()


def create_run(client, headers, year=2026, month=2):
    return client.post("/payroll/runs", json={"year": year, "month": month}, headers=headers)


def slips(client, headers, run_id):
    body = client.get(f"/payroll/runs/{run_id}/payslips?limit=100", headers=headers).json()
    return {item["employee"]["full_name"]: item for item in body["items"]}


def finalize(client, headers, run_id):
    return client.post(f"/payroll/runs/{run_id}/finalize", headers=headers)


# ---------- The numbers ----------

def test_perfect_attendance_pays_the_gross_minus_tax(client, hr, make_employee, db_session):
    _, emp = make_employee()
    attend(db_session, emp, *FEB)

    r = create_run(client, hr)
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "draft"
    assert run["period"] == "2026-02"
    assert run["working_days"] == 20
    assert run["month_in_progress"] is False
    assert run["created_by"] == "hr@t.com"
    assert run["finalized_by"] is None
    assert run["totals"] == {
        "employees": 1, "gross_salary": 75000, "prorated_gross": 75000,
        "attendance_deduction": 0, "tax": 1250, "net_pay": 73750,
    }

    slip = slips(client, hr, run["id"])["Ali Khan"]
    assert (slip["basic"], slip["hra"], slip["medical"], slip["gross_salary"]) == (50000, 20000, 5000, 75000)
    assert (slip["working_days"], slip["payable_days"]) == (20, 20)
    assert (slip["absent_days"], slip["unpaid_days"], slip["half_days"], slip["deduction_days"]) == (0, 0, 0, 0)
    assert slip["per_day_rate"] == 3750
    assert slip["net_pay"] == 73750
    assert slip["employee"]["employee_code"] == "EMP-0001"
    assert slip["department"] == "Engineering"
    assert slip["designation"] == "Developer"
    assert slip["run_status"] == "draft"


def test_absence_unpaid_leave_and_half_days_are_deducted(client, hr, make_employee, db_session, types):
    _, emp = make_employee()
    absent = {date(2026, 2, 3), date(2026, 2, 4)}
    half = {date(2026, 2, 10), date(2026, 2, 11)}
    unpaid = {date(2026, 2, 17), date(2026, 2, 18)}
    paid = {date(2026, 2, 19)}
    attend(db_session, emp, *FEB, skip=absent | half | unpaid | paid)
    for day in half:
        add_record(db_session, emp, day, DayStatus.half_day)
    add_leave(db_session, emp, types["unpaid"], date(2026, 2, 17), date(2026, 2, 18), 2)
    add_leave(db_session, emp, types["paid"], date(2026, 2, 19), date(2026, 2, 19), 1)

    run = create_run(client, hr).json()
    slip = slips(client, hr, run["id"])["Ali Khan"]
    assert slip["absent_days"] == 2
    assert slip["half_days"] == 2
    assert slip["unpaid_days"] == 2        # paid leave on the 19th costs nothing
    assert slip["deduction_days"] == 5     # 2 + 2 + 0.5 x 2
    assert slip["attendance_deduction"] == 18750
    assert slip["prorated_gross"] == 75000
    assert slip["tax"] == 1250
    assert slip["net_pay"] == 55000


def test_a_pending_leave_request_does_not_excuse_an_absence(client, hr, make_employee, db_session, types):
    _, emp = make_employee()
    day = date(2026, 2, 5)
    attend(db_session, emp, *FEB, skip={day})
    add_leave(db_session, emp, types["unpaid"], day, day, 1, status=LeaveStatus.pending)

    slip = slips(client, hr, create_run(client, hr).json()["id"])["Ali Khan"]
    assert slip["absent_days"] == 1
    assert slip["unpaid_days"] == 0
    assert slip["net_pay"] == 70000  # 75,000 - 3,750 - 1,250


def test_a_real_check_in_beats_unpaid_leave(client, hr, make_employee, db_session, types):
    _, emp = make_employee()
    attend(db_session, emp, *FEB)  # includes the 6th
    add_leave(db_session, emp, types["unpaid"], date(2026, 2, 6), date(2026, 2, 6), 1)

    slip = slips(client, hr, create_run(client, hr).json()["id"])["Ali Khan"]
    assert slip["unpaid_days"] == 0
    assert slip["attendance_deduction"] == 0
    assert slip["net_pay"] == 73750


def test_missing_checkout_and_weekend_records_cost_nothing(client, hr, make_employee, db_session):
    _, emp = make_employee()
    attend(db_session, emp, *FEB, skip={date(2026, 2, 9)})
    add_record(db_session, emp, date(2026, 2, 9), closed=False)               # forgot to check out
    add_record(db_session, emp, date(2026, 2, 7), DayStatus.half_day)         # a Saturday

    slip = slips(client, hr, create_run(client, hr).json()["id"])["Ali Khan"]
    assert slip["absent_days"] == 0
    assert slip["half_days"] == 0
    assert slip["deduction_days"] == 0
    assert slip["net_pay"] == 73750


def test_joiners_are_prorated_and_only_absences_after_joining_count(client, hr, make_employee, db_session):
    start = date(2026, 2, 11)  # a Wednesday: 13 working days left in the month
    _, one = make_employee(email="n1@t.com", name="New One", join=start)
    _, two = make_employee(email="n2@t.com", name="New Two", join=start)
    attend(db_session, one, start, FEB[1])
    attend(db_session, two, start, FEB[1], skip={date(2026, 2, 12)})

    result = slips(client, hr, create_run(client, hr).json()["id"])
    first, second = result["New One"], result["New Two"]
    assert (first["working_days"], first["payable_days"]) == (20, 13)
    assert first["absent_days"] == 0           # nothing counts before the joining date
    assert first["prorated_gross"] == 48750    # 75,000 x 13 / 20
    assert first["tax"] == 1250                # tax stays on the full monthly gross
    assert first["net_pay"] == 47500
    assert second["absent_days"] == 1
    assert second["attendance_deduction"] == 3750
    assert second["net_pay"] == 43750          # 48,750 - 3,750 - 1,250


def test_leavers_are_prorated(client, hr, make_employee, db_session):
    _, emp = make_employee(exit_date=date(2026, 2, 13))  # a Friday: 10 working days
    attend(db_session, emp, FEB[0], date(2026, 2, 13))

    slip = slips(client, hr, create_run(client, hr).json()["id"])["Ali Khan"]
    assert slip["payable_days"] == 10
    assert slip["absent_days"] == 0            # nothing counts after the exit date
    assert slip["prorated_gross"] == 37500
    assert slip["net_pay"] == 36250


def test_people_who_were_not_employed_that_month_are_left_out(client, hr, make_employee, db_session):
    _, normal = make_employee(email="a@t.com", name="Normal")
    attend(db_session, normal, *FEB)
    make_employee(email="b@t.com", name="Future Joiner", join=date(2026, 3, 1))
    make_employee(email="c@t.com", name="Old Leaver", exit_date=date(2026, 1, 31))
    make_employee(email="d@t.com", name="Sunday Leaver", exit_date=date(2026, 2, 1))     # no working day left
    make_employee(email="e@t.com", name="Saturday Joiner", join=date(2026, 2, 28))       # no working day yet

    run = create_run(client, hr).json()
    assert list(slips(client, hr, run["id"])) == ["Normal"]
    assert run["totals"]["employees"] == 1


def test_a_holiday_lowers_the_working_days(client, hr, make_employee, db_session):
    _, emp = make_employee()
    holiday = date(2026, 2, 16)
    db_session.add(Holiday(name="Special Day", date=holiday))
    db_session.commit()
    attend(db_session, emp, *FEB, skip={holiday})

    run = create_run(client, hr).json()
    slip = slips(client, hr, run["id"])["Ali Khan"]
    assert run["working_days"] == 19
    assert slip["per_day_rate"] == 3947.37     # 75,000 / 19
    assert slip["absent_days"] == 0
    assert slip["net_pay"] == 73750


def test_the_company_weekend_setting_is_used(client, hr, make_employee, db_session):
    make_employee()  # no attendance at all
    cfg = settings_service.get_settings(db_session)
    cfg.weekend_days = [6]  # only Sunday is a weekend, so Saturdays are working days
    db_session.commit()

    run = create_run(client, hr).json()
    slip = slips(client, hr, run["id"])["Ali Khan"]
    assert run["working_days"] == 24
    assert slip["absent_days"] == 24
    assert slip["attendance_deduction"] == 75000
    assert slip["tax"] == 1250
    assert slip["net_pay"] == 0                # never negative


def test_the_current_month_only_counts_days_that_are_over(client, hr, make_employee, db_session):
    _, emp = make_employee()
    add_record(db_session, emp, date(2026, 11, 2))
    add_record(db_session, emp, date(2026, 11, 3))  # the 4th has no record, and today (the 5th) is not over

    run = create_run(client, hr, month=11).json()
    slip = slips(client, hr, run["id"])["Ali Khan"]
    assert run["month_in_progress"] is True
    assert run["as_of_date"] == "2026-11-05"
    assert run["working_days"] == 21
    assert slip["absent_days"] == 1
    assert slip["attendance_deduction"] == 3571.43   # 75,000 / 21
    assert slip["net_pay"] == 70178.57


# ---------- Creating, recalculating, deleting ----------

def test_bad_months_and_input(client, hr, make_employee):
    make_employee()
    assert create_run(client, hr, 2026, 12).status_code == 400   # next month
    assert create_run(client, hr, 2027, 1).status_code == 400
    for bad in ({"year": 2026, "month": 13}, {"year": 2026, "month": 0}, {"year": 1999, "month": 5},
                {"year": "abc", "month": 5}, {"month": 5}, {"year": 2026}, {}):
        assert client.post("/payroll/runs", json=bad, headers=hr).status_code == 422, bad


def test_setup_and_employees_are_needed(client, hr, db_session):
    assert create_run(client, hr).status_code == 409             # no company yet
    db_session.add(Company(name="Test Co"))
    db_session.commit()
    assert create_run(client, hr).status_code == 400             # no employees to pay
    assert client.get("/payroll/runs", headers=hr).json()["total"] == 0  # and nothing was left behind


def test_one_run_per_month(client, hr, make_employee):
    make_employee()
    assert create_run(client, hr, 2026, 2).status_code == 201
    assert create_run(client, hr, 2026, 2).status_code == 409
    assert create_run(client, hr, 2026, 1).status_code == 201


def test_recalculate_picks_up_new_data(client, hr, make_employee, db_session):
    _, emp = make_employee()
    attend(db_session, emp, *FEB, skip={date(2026, 2, 3)})
    run = create_run(client, hr).json()
    assert slips(client, hr, run["id"])["Ali Khan"]["net_pay"] == 70000

    add_record(db_session, emp, date(2026, 2, 3))  # the missing day is fixed
    r = client.post(f"/payroll/runs/{run['id']}/recalculate", headers=hr)
    assert r.status_code == 200
    assert r.json()["totals"]["net_pay"] == 73750
    assert slips(client, hr, run["id"])["Ali Khan"]["absent_days"] == 0

    # A new salary only shows up after the next recalculation
    assert client.put(f"/payroll/salary-structure/{emp.id}", json={"basic_salary": 100000}, headers=hr).status_code == 200
    assert slips(client, hr, run["id"])["Ali Khan"]["basic"] == 50000
    client.post(f"/payroll/runs/{run['id']}/recalculate", headers=hr)
    after = slips(client, hr, run["id"])["Ali Khan"]
    assert (after["gross_salary"], after["per_day_rate"], after["tax"], after["net_pay"]) == (150000, 7500, 10000, 140000)


def test_delete_a_draft(client, hr, admin, make_employee):
    make_employee()
    run = create_run(client, hr).json()
    assert client.delete(f"/payroll/runs/{run['id']}", headers=admin).status_code == 403
    assert client.delete(f"/payroll/runs/{run['id']}", headers=hr).status_code == 204
    assert client.get(f"/payroll/runs/{run['id']}", headers=hr).status_code == 404
    assert create_run(client, hr).status_code == 201             # the month is free again


# ---------- Finalizing and locking ----------

def test_only_admin_finalizes_and_it_locks_the_run(client, hr, admin, make_employee, db_session):
    user, emp = make_employee()
    attend(db_session, emp, *FEB)
    run = create_run(client, hr).json()
    rid = run["id"]

    assert finalize(client, hr, rid).status_code == 403
    assert finalize(client, auth(user), rid).status_code == 403
    assert client.post(f"/payroll/runs/{rid}/finalize").status_code == 401

    done = finalize(client, admin, rid)
    assert done.status_code == 200
    assert done.json()["status"] == "finalized"
    assert done.json()["finalized_by"] == "admin@t.com"
    assert done.json()["finalized_at"] is not None

    # Locked in every way
    assert finalize(client, admin, rid).status_code == 409
    assert client.post(f"/payroll/runs/{rid}/recalculate", headers=hr).status_code == 409
    assert client.delete(f"/payroll/runs/{rid}", headers=hr).status_code == 409
    assert create_run(client, hr).status_code == 409

    # Later changes do not touch a finalized payslip
    assert client.put(f"/payroll/salary-structure/{emp.id}", json={"basic_salary": 100000}, headers=hr).status_code == 200
    emp.full_name = "Renamed Person"
    db_session.commit()
    slip = slips(client, hr, rid)["Ali Khan"]
    assert slip["basic"] == 50000
    assert slip["net_pay"] == 73750
    assert slip["run_status"] == "finalized"


def test_unknown_run_is_404(client, hr, admin):
    assert client.get("/payroll/runs/999", headers=hr).status_code == 404
    assert client.get("/payroll/runs/999/payslips", headers=hr).status_code == 404
    assert client.post("/payroll/runs/999/recalculate", headers=hr).status_code == 404
    assert client.post("/payroll/runs/999/finalize", headers=admin).status_code == 404
    assert client.delete("/payroll/runs/999", headers=hr).status_code == 404


def test_who_can_do_what_with_runs(client, hr, admin, make_employee, db_session):
    user, emp = make_employee()
    attend(db_session, emp, *FEB)
    employee = auth(user)
    body = {"year": 2026, "month": 2}

    assert client.post("/payroll/runs", json=body, headers=admin).status_code == 403   # Admin only views and finalizes
    assert client.post("/payroll/runs", json=body, headers=employee).status_code == 403
    assert client.post("/payroll/runs", json=body).status_code == 401
    rid = create_run(client, hr).json()["id"]

    for path in ("/payroll/runs", f"/payroll/runs/{rid}", f"/payroll/runs/{rid}/payslips"):
        assert client.get(path, headers=hr).status_code == 200
        assert client.get(path, headers=admin).status_code == 200
        assert client.get(path, headers=employee).status_code == 403
        assert client.get(path).status_code == 401

    assert client.post(f"/payroll/runs/{rid}/recalculate", headers=admin).status_code == 403
    assert client.post(f"/payroll/runs/{rid}/recalculate", headers=employee).status_code == 403
    assert client.delete(f"/payroll/runs/{rid}", headers=employee).status_code == 403


# ---------- Lists ----------

def test_run_list_filters_and_paging(client, hr, admin, make_employee):
    make_employee()
    ids = {m: create_run(client, hr, 2026, m).json()["id"] for m in (1, 2, 3)}
    assert finalize(client, admin, ids[2]).status_code == 200

    everything = client.get("/payroll/runs", headers=hr).json()
    assert everything["total"] == 3
    assert [i["period"] for i in everything["items"]] == ["2026-03", "2026-02", "2026-01"]  # newest first
    assert everything["items"][0]["totals"]["employees"] == 1

    def get(query):
        return client.get(f"/payroll/runs?{query}", headers=hr).json()

    assert get("status=finalized")["total"] == 1
    assert get("status=finalized")["items"][0]["period"] == "2026-02"
    assert get("status=draft")["total"] == 2
    assert get("year=2026")["total"] == 3
    assert get("year=2025")["total"] == 0
    page = get("limit=2&offset=2")
    assert len(page["items"]) == 1 and page["total"] == 3

    for bad in ("status=bogus", "limit=0", "limit=1000", "offset=-1", "year=1999"):
        assert client.get(f"/payroll/runs?{bad}", headers=hr).status_code == 422


def test_payslip_list_filters_and_paging(client, hr, make_employee, db_session):
    sales = Department(name="Sales")
    db_session.add(sales)
    db_session.commit()
    make_employee(email="a@t.com", name="Ann")
    make_employee(email="b@t.com", name="Bob", department_id=sales.id)
    make_employee(email="c@t.com", name="Cid")
    rid = create_run(client, hr).json()["id"]

    everything = client.get(f"/payroll/runs/{rid}/payslips", headers=hr).json()
    assert everything["total"] == 3
    assert [i["employee"]["full_name"] for i in everything["items"]] == ["Ann", "Bob", "Cid"]

    def total(query):
        return client.get(f"/payroll/runs/{rid}/payslips?{query}", headers=hr).json()["total"]

    assert total(f"department_id={sales.id}") == 1
    assert total("search=bob") == 1
    assert total("search=EMP-0003") == 1
    page = client.get(f"/payroll/runs/{rid}/payslips?limit=2&offset=2", headers=hr).json()
    assert len(page["items"]) == 1 and page["total"] == 3

    for bad in ("limit=0", "limit=1000", "offset=-1"):
        assert client.get(f"/payroll/runs/{rid}/payslips?{bad}", headers=hr).status_code == 422


# ---------- Payslips for employees ----------

def test_employees_only_see_their_own_finalized_payslips(client, hr, admin, make_employee, make_user, db_session):
    ann, ann_emp = make_employee(email="a@t.com", name="Ann")
    bob, bob_emp = make_employee(email="b@t.com", name="Bob")
    attend(db_session, ann_emp, *FEB)
    attend(db_session, bob_emp, *FEB)
    run = create_run(client, hr).json()
    ids = {name: item["id"] for name, item in slips(client, hr, run["id"]).items()}

    # A draft is invisible to employees
    assert client.get("/payroll/payslips/me", headers=auth(ann)).json()["total"] == 0
    assert client.get(f"/payroll/payslips/{ids['Ann']}", headers=auth(ann)).status_code == 404
    assert client.get(f"/payroll/payslips/{ids['Ann']}/pdf", headers=auth(ann)).status_code == 404

    assert finalize(client, admin, run["id"]).status_code == 200

    mine = client.get("/payroll/payslips/me", headers=auth(ann)).json()
    assert mine["total"] == 1
    assert mine["items"][0]["employee"]["full_name"] == "Ann"
    assert mine["items"][0]["net_pay"] == 73750
    assert mine["items"][0]["run_status"] == "finalized"
    assert client.get(f"/payroll/payslips/{ids['Ann']}", headers=auth(ann)).status_code == 200
    assert client.get(f"/payroll/payslips/{ids['Ann']}/pdf", headers=auth(ann)).status_code == 200

    # Never someone else's, and never anything that does not exist
    assert client.get(f"/payroll/payslips/{ids['Bob']}", headers=auth(ann)).status_code == 404
    assert client.get(f"/payroll/payslips/{ids['Bob']}/pdf", headers=auth(ann)).status_code == 404
    assert client.get(f"/payroll/payslips/{ids['Ann']}", headers=auth(bob)).status_code == 404
    assert client.get("/payroll/payslips/999", headers=auth(ann)).status_code == 404

    assert client.get("/payroll/payslips/me?year=2026", headers=auth(ann)).json()["total"] == 1
    assert client.get("/payroll/payslips/me?year=2025", headers=auth(ann)).json()["total"] == 0
    for bad in ("year=1999", "limit=0", "limit=1000", "offset=-1"):
        assert client.get(f"/payroll/payslips/me?{bad}", headers=auth(ann)).status_code == 422

    # Staff have no personal payslips, and a login without an employee record has none either
    assert client.get("/payroll/payslips/me", headers=hr).status_code == 403
    assert client.get("/payroll/payslips/me", headers=admin).status_code == 403
    assert client.get("/payroll/payslips/me").status_code == 401
    no_profile = auth(make_user(email="x@t.com", role=Role.employee))
    assert client.get("/payroll/payslips/me", headers=no_profile).status_code == 404


def test_staff_can_open_any_payslip_even_a_draft(client, hr, admin, make_employee):
    make_employee()
    rid = create_run(client, hr).json()["id"]
    pid = slips(client, hr, rid)["Ali Khan"]["id"]
    for headers in (hr, admin):
        assert client.get(f"/payroll/payslips/{pid}", headers=headers).status_code == 200
        assert client.get(f"/payroll/payslips/{pid}/pdf", headers=headers).status_code == 200
    assert client.get("/payroll/payslips/999", headers=hr).status_code == 404
    assert client.get(f"/payroll/payslips/{pid}").status_code == 401
    assert client.get(f"/payroll/payslips/{pid}/pdf").status_code == 401


def test_the_pdf_download(client, hr, admin, make_employee, db_session):
    user, emp = make_employee()
    attend(db_session, emp, *FEB)
    rid = create_run(client, hr).json()["id"]
    pid = slips(client, hr, rid)["Ali Khan"]["id"]
    finalize(client, admin, rid)

    r = client.get(f"/payroll/payslips/{pid}/pdf", headers=auth(user))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="payslip-EMP-0001-2026-02.pdf"'
    assert r.content.startswith(b"%PDF")


# ---------- Salary structure ----------

def test_salary_structure_view_and_update(client, hr, admin, make_employee):
    user, emp = make_employee()
    path = f"/payroll/salary-structure/{emp.id}"

    for headers in (hr, admin):
        body = client.get(path, headers=headers).json()
        assert (body["basic_salary"], body["hra"], body["medical"], body["gross_salary"]) == (50000, 20000, 5000, 75000)
        assert body["annual_gross"] == 900000
        assert body["estimated_monthly_tax"] == 1250
        assert body["employee"]["full_name"] == "Ali Khan"

    updated = client.put(path, json={"basic_salary": 80000}, headers=hr).json()
    assert (updated["hra"], updated["medical"], updated["gross_salary"]) == (32000, 8000, 120000)
    assert client.get(f"/employees/{emp.id}", headers=hr).json()["basic_salary"] == 80000


def test_salary_structure_rules(client, hr, admin, make_employee):
    user, emp = make_employee()
    path = f"/payroll/salary-structure/{emp.id}"
    body = {"basic_salary": 60000}

    assert client.put(path, json=body, headers=admin).status_code == 403   # only HR changes salaries
    assert client.put(path, json=body, headers=auth(user)).status_code == 403
    assert client.get(path, headers=auth(user)).status_code == 403
    assert client.put(path, json=body).status_code == 401
    assert client.get(path).status_code == 401
    assert client.get("/payroll/salary-structure/999", headers=hr).status_code == 404
    assert client.put("/payroll/salary-structure/999", json=body, headers=hr).status_code == 404

    for bad in ({"basic_salary": 0}, {"basic_salary": -5}, {"basic_salary": 10**9}, {"basic_salary": "abc"}, {}):
        assert client.put(path, json=bad, headers=hr).status_code == 422, bad


def test_an_exited_employees_salary_cannot_change(client, hr, make_employee):
    _, emp = make_employee(exit_date=date(2026, 2, 13))
    r = client.put(f"/payroll/salary-structure/{emp.id}", json={"basic_salary": 60000}, headers=hr)
    assert r.status_code == 409