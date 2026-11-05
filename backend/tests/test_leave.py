from datetime import date, datetime, timezone

import pytest

from app.core import clock
from app.core.security import create_access_token
from app.models.attendance import Attendance, DayStatus
from app.models.company import Company
from app.models.employee import Employee, EmployeeStatus
from app.models.leave_config import Holiday, LeaveType
from app.models.org import Department, Designation
from app.models.user import Role
from app.services import settings_service

# "Now" is Thursday 2026-11-05, 10:00 office time.
# Weekdays: Mon 2, 9, 16, 23  ...  Sat 7, 14, 21  Sun 1, 8, 15, 22


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
    rows = {
        "Annual": LeaveType(name="Annual", yearly_quota=14, is_paid=True),
        "Sick": LeaveType(name="Sick", yearly_quota=8, is_paid=True),
        "Casual": LeaveType(name="Casual", yearly_quota=10, is_paid=True),
        "Unpaid": LeaveType(name="Unpaid", yearly_quota=None, is_paid=False),
    }
    db_session.add_all(rows.values())
    db_session.commit()
    return {name: row.id for name, row in rows.items()}


@pytest.fixture()
def make_employee(db_session, make_user, org):
    counter = {"n": 0}

    def _make(email="emp@t.com", name="Ali Khan", join=date(2026, 1, 1), exit_date=None, department_id=None):
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
            basic_salary=50000,
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


def apply(client, user, type_id, start, end, reason="Family event"):
    body = {"leave_type_id": type_id, "start_date": start, "end_date": end, "reason": reason}
    return client.post("/leave", json=body, headers=auth(user))


def approve(client, hr, leave_id, comment=None):
    payload = {"comment": comment} if comment else None
    return client.post(f"/leave/{leave_id}/approve", json=payload, headers=hr)


def reject(client, hr, leave_id, comment="Team is short that week"):
    return client.post(f"/leave/{leave_id}/reject", json={"comment": comment}, headers=hr)


def cancel(client, user, leave_id):
    return client.post(f"/leave/{leave_id}/cancel", headers=auth(user))


def balance(client, user, year=None):
    path = "/leave/balance" + (f"?year={year}" if year else "")
    items = client.get(path, headers=auth(user)).json()["items"]
    return {item["leave_type"]: item for item in items}


def month_days(client, user, month="2026-11"):
    body = client.get(f"/attendance/me?month={month}", headers=auth(user)).json()
    return {d["date"]: d["status"] for d in body["days"]}, body["summary"]


def add_record(db, emp, day, status):
    db.add(Attendance(
        employee_id=emp.id,
        date=day,
        check_in=datetime(day.year, day.month, day.day, 4, 0, tzinfo=timezone.utc),   # 09:00 office time
        check_out=datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc),  # 17:00 office time
        status=status,
        is_late=False,
        source="web",
    ))
    db.commit()


# ---------- Applying: counting working days ----------

def test_apply_counts_working_days(client, make_employee, types):
    user, _ = make_employee()
    r = apply(client, user, types["Annual"], "2026-11-09", "2026-11-13")  # Monday to Friday
    assert r.status_code == 201
    body = r.json()
    assert body["days"] == 5
    assert body["status"] == "pending"
    assert body["leave_type"] == {"id": types["Annual"], "name": "Annual", "is_paid": True}
    assert body["employee"]["full_name"] == "Ali Khan"
    assert body["reason"] == "Family event"
    assert body["reviewed_by"] is None
    assert body["review_comment"] is None

    across = apply(client, user, types["Casual"], "2026-11-18", "2026-11-24")  # Wed 18 to Tue 24, over a weekend
    assert across.json()["days"] == 5


def test_reason_is_cleaned(client, make_employee, types):
    user, _ = make_employee()
    r = apply(client, user, types["Annual"], "2026-11-09", "2026-11-09", reason="  Family   event  ")
    assert r.json()["reason"] == "Family event"


def test_weekends_and_holidays_are_skipped(client, make_employee, db_session, types):
    user, _ = make_employee()
    db_session.add(Holiday(name="Founders Day", date=date(2026, 11, 12)))
    db_session.commit()
    r = apply(client, user, types["Annual"], "2026-11-11", "2026-11-16")  # Wed, Thu (holiday), Fri, Sat, Sun, Mon
    assert r.json()["days"] == 3


def test_dates_with_no_working_days_are_422(client, make_employee, db_session, types):
    user, _ = make_employee()
    db_session.add(Holiday(name="Special Day", date=date(2026, 11, 10)))
    db_session.commit()
    assert apply(client, user, types["Annual"], "2026-11-14", "2026-11-15").status_code == 422  # Saturday and Sunday
    assert apply(client, user, types["Annual"], "2026-11-10", "2026-11-10").status_code == 422  # a holiday
    assert client.get("/leave/me", headers=auth(user)).json()["total"] == 0


def test_company_weekend_setting_is_used(client, make_employee, db_session, types):
    user, _ = make_employee()
    cfg = settings_service.get_settings(db_session)
    cfg.weekend_days = [6]  # only Sunday is a weekend
    db_session.commit()
    r = apply(client, user, types["Annual"], "2026-11-13", "2026-11-16")  # Fri, Sat, Sun, Mon
    assert r.json()["days"] == 3


# ---------- Applying: overlap and balance ----------

def test_overlap_is_422(client, hr, make_employee, types):
    user, _ = make_employee()
    other, _ = make_employee(email="o@t.com", name="Other")
    first = apply(client, user, types["Annual"], "2026-11-09", "2026-11-11").json()  # Mon to Wed

    assert apply(client, user, types["Casual"], "2026-11-11", "2026-11-12").status_code == 422  # shares Wednesday
    assert apply(client, user, types["Annual"], "2026-11-12", "2026-11-13").status_code == 201
    assert apply(client, other, types["Annual"], "2026-11-09", "2026-11-11").status_code == 201  # another person is fine

    assert approve(client, hr, first["id"]).status_code == 200
    assert apply(client, user, types["Annual"], "2026-11-10", "2026-11-10").status_code == 422  # approved leave blocks too

    assert cancel(client, user, first["id"]).status_code == 200
    assert apply(client, user, types["Annual"], "2026-11-10", "2026-11-10").status_code == 201  # a cancelled request frees the dates


def test_over_balance_is_422(client, make_employee, types):
    user, _ = make_employee()
    assert apply(client, user, types["Sick"], "2026-11-09", "2026-11-13").status_code == 201  # 5 of 8 days

    too_much = apply(client, user, types["Sick"], "2026-11-16", "2026-11-19")  # 4 days, only 3 left
    assert too_much.status_code == 422
    assert "Not enough balance" in too_much.json()["detail"]

    assert apply(client, user, types["Sick"], "2026-11-16", "2026-11-18").status_code == 201  # exactly 3 days
    assert apply(client, user, types["Sick"], "2026-11-19", "2026-11-19").status_code == 422  # nothing left
    assert client.get("/leave/me", headers=auth(user)).json()["total"] == 2


def test_unpaid_leave_is_unlimited(client, make_employee, types):
    user, _ = make_employee()
    r = apply(client, user, types["Unpaid"], "2026-11-09", "2026-12-18")  # six full working weeks
    assert r.status_code == 201
    assert r.json()["days"] == 30
    assert r.json()["leave_type"]["is_paid"] is False


def test_leave_cannot_span_two_years(client, make_employee, types):
    user, _ = make_employee()
    assert apply(client, user, types["Annual"], "2026-12-30", "2027-01-04").status_code == 422
    assert apply(client, user, types["Annual"], "2026-12-30", "2026-12-31").status_code == 201
    assert apply(client, user, types["Annual"], "2027-01-04", "2027-01-05").status_code == 201


# ---------- Applying: date and input limits ----------

def test_date_limits(client, make_employee, types):
    user, _ = make_employee()
    assert apply(client, user, types["Annual"], "2026-10-05", "2026-10-05").status_code == 400  # 31 days back
    assert apply(client, user, types["Annual"], "2026-10-06", "2026-10-06").status_code == 201  # exactly 30 days back
    assert apply(client, user, types["Annual"], "2027-11-08", "2027-11-08").status_code == 400  # more than a year ahead


def test_dates_must_fit_the_employment(client, make_employee, types):
    new, _ = make_employee(email="n@t.com", name="New", join=date(2026, 11, 2))
    assert apply(client, new, types["Annual"], "2026-10-30", "2026-11-03").status_code == 400  # starts before joining
    assert apply(client, new, types["Annual"], "2026-11-02", "2026-11-03").status_code == 201

    leaver, _ = make_employee(email="l@t.com", name="Lee", exit_date=date(2026, 11, 12))
    assert apply(client, leaver, types["Annual"], "2026-11-09", "2026-11-13").status_code == 400  # past the exit date
    assert apply(client, leaver, types["Annual"], "2026-11-09", "2026-11-12").status_code == 201


def test_unknown_or_inactive_leave_type_is_400(client, make_employee, db_session, types):
    user, _ = make_employee()
    assert apply(client, user, 999, "2026-11-09", "2026-11-09").status_code == 400
    casual = db_session.get(LeaveType, types["Casual"])
    casual.is_active = False
    db_session.commit()
    assert apply(client, user, types["Casual"], "2026-11-09", "2026-11-09").status_code == 400


@pytest.mark.parametrize(
    "over",
    [
        {"reason": "ab"},
        {"reason": "     "},
        {"reason": "x" * 501},
        {"start_date": "garbage"},
        {"end_date": "2026-13-45"},
        {"start_date": "2026-11-13", "end_date": "2026-11-09"},
        {"leave_type_id": "abc"},
    ],
)
def test_bad_input_is_422(client, make_employee, types, over):
    user, _ = make_employee()
    body = {
        "leave_type_id": types["Annual"],
        "start_date": "2026-11-09",
        "end_date": "2026-11-13",
        "reason": "Family event",
        **over,
    }
    assert client.post("/leave", json=body, headers=auth(user)).status_code == 422


@pytest.mark.parametrize("missing", ["leave_type_id", "start_date", "end_date", "reason"])
def test_missing_fields_are_422(client, make_employee, types, missing):
    user, _ = make_employee()
    body = {
        "leave_type_id": types["Annual"],
        "start_date": "2026-11-09",
        "end_date": "2026-11-13",
        "reason": "Family event",
    }
    del body[missing]
    assert client.post("/leave", json=body, headers=auth(user)).status_code == 422


# ---------- Balance ----------

def test_balance_follows_the_requests(client, hr, make_employee, types):
    user, _ = make_employee()
    start = balance(client, user)
    assert list(start) == ["Annual", "Casual", "Sick", "Unpaid"]
    annual = start["Annual"]
    assert (annual["yearly_quota"], annual["used"], annual["pending"], annual["remaining"]) == (14, 0, 0, 14)
    assert start["Unpaid"]["is_unlimited"] is True
    assert start["Unpaid"]["remaining"] is None
    assert start["Unpaid"]["is_paid"] is False

    leave = apply(client, user, types["Annual"], "2026-11-09", "2026-11-13").json()  # 5 days
    row = balance(client, user)["Annual"]
    assert (row["used"], row["pending"], row["remaining"]) == (0, 5, 9)  # pending days are reserved

    approve(client, hr, leave["id"])
    row = balance(client, user)["Annual"]
    assert (row["used"], row["pending"], row["remaining"]) == (5, 0, 9)

    sick = apply(client, user, types["Sick"], "2026-11-16", "2026-11-17").json()  # 2 days
    row = balance(client, user)["Sick"]
    assert (row["used"], row["pending"], row["remaining"]) == (0, 2, 6)
    reject(client, hr, sick["id"])
    row = balance(client, user)["Sick"]
    assert (row["used"], row["pending"], row["remaining"]) == (0, 0, 8)  # a rejection gives the days back

    casual = apply(client, user, types["Casual"], "2026-11-18", "2026-11-18").json()
    cancel(client, user, casual["id"])
    row = balance(client, user)["Casual"]
    assert (row["used"], row["pending"], row["remaining"]) == (0, 0, 10)  # so does a cancellation

    other_year = client.get("/leave/balance?year=2027", headers=auth(user)).json()
    assert other_year["year"] == 2027
    assert all(i["used"] == 0 and i["pending"] == 0 for i in other_year["items"])


def test_balance_is_per_year(client, make_employee, types):
    user, _ = make_employee()
    apply(client, user, types["Annual"], "2027-01-04", "2027-01-05")  # 2 days in 2027
    assert balance(client, user, 2027)["Annual"]["pending"] == 2
    assert balance(client, user, 2026)["Annual"]["pending"] == 0


def test_balance_hides_inactive_types_and_checks_the_year(client, make_employee, db_session, types):
    user, _ = make_employee()
    casual = db_session.get(LeaveType, types["Casual"])
    casual.is_active = False
    db_session.commit()
    assert "Casual" not in balance(client, user)
    assert client.get("/leave/balance?year=1999", headers=auth(user)).status_code == 422
    assert client.get("/leave/balance?year=abc", headers=auth(user)).status_code == 422


def test_staff_can_see_an_employees_balance(client, hr, admin, make_employee, types):
    user, emp = make_employee()
    employee_token = auth(user)
    apply(client, user, types["Annual"], "2026-11-09", "2026-11-10")  # 2 days
    for headers in (hr, admin):
        r = client.get(f"/leave/employees/{emp.id}/balance", headers=headers)
        assert r.status_code == 200
        annual = next(i for i in r.json()["items"] if i["leave_type"] == "Annual")
        assert annual["pending"] == 2
    assert client.get("/leave/employees/999/balance", headers=hr).status_code == 404
    assert client.get(f"/leave/employees/{emp.id}/balance", headers=employee_token).status_code == 403
    assert client.get(f"/leave/employees/{emp.id}/balance").status_code == 401


# ---------- Preview ----------

def test_preview(client, make_employee, types):
    user, _ = make_employee()

    def preview(type_name, start, end):
        url = f"/leave/preview?leave_type_id={types[type_name]}&start_date={start}&end_date={end}"
        return client.get(url, headers=auth(user))

    r = preview("Annual", "2026-11-09", "2026-11-13")
    assert r.status_code == 200
    assert r.json() == {"working_days": 5, "yearly_quota": 14, "available_before": 14, "available_after": 9}

    r = preview("Unpaid", "2026-11-09", "2026-11-13")
    assert r.json() == {"working_days": 5, "yearly_quota": None, "available_before": None, "available_after": None}

    assert preview("Sick", "2026-11-09", "2026-11-19").status_code == 422   # 9 days, 8 allowed
    assert preview("Annual", "2026-11-14", "2026-11-15").status_code == 422  # weekend only
    assert preview("Annual", "2026-11-13", "2026-11-09").status_code == 400  # end before start
    assert client.get("/leave/preview?leave_type_id=1", headers=auth(user)).status_code == 422  # dates missing
    assert client.get("/leave/me", headers=auth(user)).json()["total"] == 0  # a preview creates nothing


# ---------- Cancelling ----------

def test_cancel_pending_and_upcoming_approved_leave(client, hr, make_employee, types):
    user, _ = make_employee()
    pending = apply(client, user, types["Annual"], "2026-11-09", "2026-11-13").json()
    r = cancel(client, user, pending["id"])
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert balance(client, user)["Annual"]["remaining"] == 14

    upcoming = apply(client, user, types["Annual"], "2026-11-09", "2026-11-13").json()  # the dates are free again
    approve(client, hr, upcoming["id"])
    assert balance(client, user)["Annual"]["remaining"] == 9
    assert cancel(client, user, upcoming["id"]).status_code == 200  # not started yet
    assert balance(client, user)["Annual"]["remaining"] == 14


def test_started_leave_cannot_be_cancelled(client, hr, make_employee, types):
    user, _ = make_employee()
    started = apply(client, user, types["Annual"], "2026-11-05", "2026-11-06").json()  # starts today
    approve(client, hr, started["id"])
    assert cancel(client, user, started["id"]).status_code == 409
    assert balance(client, user)["Annual"]["used"] == 2


def test_cancel_rules(client, hr, make_employee, types):
    user, _ = make_employee()
    other, _ = make_employee(email="o@t.com", name="Other")

    rejected = apply(client, user, types["Annual"], "2026-11-09", "2026-11-10").json()
    reject(client, hr, rejected["id"])
    assert cancel(client, user, rejected["id"]).status_code == 409  # already decided

    mine = apply(client, user, types["Annual"], "2026-11-16", "2026-11-17").json()
    assert cancel(client, other, mine["id"]).status_code == 404  # not their request
    assert cancel(client, user, 999).status_code == 404
    assert client.post(f"/leave/{mine['id']}/cancel", headers=hr).status_code == 403
    assert cancel(client, user, mine["id"]).status_code == 200
    assert cancel(client, user, mine["id"]).status_code == 409  # already cancelled


# ---------- HR decisions ----------

def test_approve_and_reject(client, hr, make_employee, types):
    user, _ = make_employee()
    first = apply(client, user, types["Annual"], "2026-11-09", "2026-11-10").json()

    r = approve(client, hr, first["id"], "Enjoy the break")
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "approved"
    assert out["review_comment"] == "Enjoy the break"
    assert out["reviewed_by"] == "hr@t.com"
    assert out["reviewed_at"] is not None

    second = apply(client, user, types["Casual"], "2026-11-16", "2026-11-16").json()
    path = f"/leave/{second['id']}/reject"
    for bad in ({}, {"comment": ""}, {"comment": "  "}, {"comment": "no"}):
        assert client.post(path, json=bad, headers=hr).status_code == 422  # a rejection needs a real comment

    r = reject(client, hr, second["id"], "Team is short that week")
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["review_comment"] == "Team is short that week"
    row = balance(client, user)["Casual"]
    assert (row["used"], row["pending"]) == (0, 0)


def test_the_approval_comment_is_optional(client, hr, make_employee, types):
    user, _ = make_employee()
    leave = apply(client, user, types["Annual"], "2026-11-09", "2026-11-10").json()
    r = approve(client, hr, leave["id"])
    assert r.status_code == 200
    assert r.json()["review_comment"] is None


def test_decisions_are_final(client, hr, make_employee, types):
    user, _ = make_employee()
    leave = apply(client, user, types["Annual"], "2026-11-09", "2026-11-10").json()
    assert approve(client, hr, leave["id"]).status_code == 200
    assert approve(client, hr, leave["id"]).status_code == 409
    assert reject(client, hr, leave["id"]).status_code == 409
    assert approve(client, hr, 999).status_code == 404
    assert reject(client, hr, 999).status_code == 404


def test_approval_rechecks_the_balance(client, hr, make_employee, db_session, types):
    user, _ = make_employee()
    first = apply(client, user, types["Casual"], "2026-11-09", "2026-11-13").json()   # 5 days
    second = apply(client, user, types["Casual"], "2026-11-16", "2026-11-20").json()  # 5 days, quota 10
    casual = db_session.get(LeaveType, types["Casual"])
    casual.yearly_quota = 8  # the quota was lowered afterwards
    db_session.commit()

    assert approve(client, hr, first["id"]).status_code == 200
    assert approve(client, hr, second["id"]).status_code == 409  # 5 + 5 would pass the new quota of 8
    pending = client.get("/leave?status=pending", headers=hr).json()
    assert pending["total"] == 1
    assert pending["items"][0]["id"] == second["id"]


def test_approval_rechecks_the_exit_date(client, hr, make_employee, db_session, types):
    user, emp = make_employee()
    leave = apply(client, user, types["Annual"], "2026-11-09", "2026-11-13").json()
    emp.exit_date = date(2026, 11, 10)
    db_session.commit()
    assert approve(client, hr, leave["id"]).status_code == 409


# ---------- Who can do what ----------

def test_who_can_do_what(client, hr, admin, make_employee, make_user, types):
    user, _ = make_employee()
    other, _ = make_employee(email="o@t.com", name="Other")
    no_profile = auth(make_user(email="x@t.com", role=Role.employee))  # an employee login with no employee record
    leave = apply(client, user, types["Annual"], "2026-11-09", "2026-11-10").json()
    body = {
        "leave_type_id": types["Annual"],
        "start_date": "2026-11-16",
        "end_date": "2026-11-17",
        "reason": "Family event",
    }
    preview_url = "/leave/preview?leave_type_id=1&start_date=2026-11-16&end_date=2026-11-17"

    # Only employees apply and see their own leave
    for headers in (hr, admin):
        assert client.post("/leave", json=body, headers=headers).status_code == 403
        assert client.get("/leave/me", headers=headers).status_code == 403
        assert client.get("/leave/balance", headers=headers).status_code == 403
        assert client.get(preview_url, headers=headers).status_code == 403
    assert client.post("/leave", json=body).status_code == 401
    assert client.get("/leave/me").status_code == 401
    assert client.get("/leave/balance").status_code == 401
    assert client.post("/leave", json=body, headers=no_profile).status_code == 404

    # Only HR decides
    for headers in (admin, auth(user)):
        assert client.post(f"/leave/{leave['id']}/approve", headers=headers).status_code == 403
        assert client.post(f"/leave/{leave['id']}/reject", json={"comment": "No way"}, headers=headers).status_code == 403
    assert client.post(f"/leave/{leave['id']}/approve").status_code == 401

    # HR and Admin read the inbox, employees cannot
    assert client.get("/leave", headers=hr).status_code == 200
    assert client.get("/leave", headers=admin).status_code == 200
    assert client.get("/leave", headers=auth(user)).status_code == 403
    assert client.get("/leave").status_code == 401

    # An employee never sees someone else's leave
    assert client.get("/leave/me", headers=auth(other)).json()["total"] == 0


# ---------- Lists ----------

def test_lists_filters_and_paging(client, hr, admin, make_employee, db_session, types):
    sales = Department(name="Sales")
    db_session.add(sales)
    db_session.commit()
    ann, ann_emp = make_employee(email="a@t.com", name="Ann")
    bob, _ = make_employee(email="b@t.com", name="Bob", department_id=sales.id)

    a1 = apply(client, ann, types["Annual"], "2026-11-09", "2026-11-10").json()
    a2 = apply(client, ann, types["Sick"], "2026-11-16", "2026-11-16").json()
    b1 = apply(client, bob, types["Annual"], "2026-11-09", "2026-11-09").json()
    jan = apply(client, ann, types["Annual"], "2027-01-04", "2027-01-04").json()
    reject(client, hr, a2["id"])

    mine = client.get("/leave/me", headers=auth(ann)).json()
    assert mine["total"] == 3
    assert {i["employee"]["full_name"] for i in mine["items"]} == {"Ann"}  # never anyone else's
    assert [i["id"] for i in mine["items"]] == [jan["id"], a2["id"], a1["id"]]  # newest first
    assert client.get("/leave/me?status=pending", headers=auth(ann)).json()["total"] == 2
    assert client.get("/leave/me?status=rejected", headers=auth(ann)).json()["total"] == 1
    assert client.get("/leave/me?year=2027", headers=auth(ann)).json()["total"] == 1
    assert client.get("/leave/me?year=2026", headers=auth(ann)).json()["total"] == 2

    everything = client.get("/leave", headers=hr).json()
    assert everything["total"] == 4
    assert everything["items"][0]["id"] == jan["id"]
    assert client.get("/leave", headers=admin).json()["total"] == 4

    def total(query):
        return client.get(f"/leave?{query}", headers=hr).json()["total"]

    assert total("status=pending") == 3
    assert total("status=rejected") == 1
    assert total(f"employee_id={ann_emp.id}") == 3
    assert total(f"leave_type_id={types['Sick']}") == 1
    assert total(f"department_id={sales.id}") == 1

    page1 = client.get("/leave?limit=2&offset=0", headers=hr).json()
    page2 = client.get("/leave?limit=2&offset=2", headers=hr).json()
    assert len(page1["items"]) == 2 and len(page2["items"]) == 2
    assert page1["total"] == 4
    assert b1["id"] in [i["id"] for i in page1["items"] + page2["items"]]

    for bad in ("status=bogus", "limit=0", "limit=1000", "offset=-1"):
        assert client.get(f"/leave?{bad}", headers=hr).status_code == 422
    assert client.get("/leave/me?year=1999", headers=auth(ann)).status_code == 422


# ---------- Attendance shows approved leave ----------

def test_approved_leave_shows_as_on_leave(client, hr, make_employee, types):
    user, _ = make_employee(join=date(2026, 10, 1))
    leave = apply(client, user, types["Annual"], "2026-11-02", "2026-11-04").json()  # Mon to Wed, already past

    days, _ = month_days(client, user)
    assert days["2026-11-03"] == "absent"  # a pending request does not count

    approve(client, hr, leave["id"])
    days, summary = month_days(client, user)
    assert days["2026-11-02"] == days["2026-11-03"] == days["2026-11-04"] == "on_leave"
    assert days["2026-11-05"] == "not_checked_in"
    assert summary["counts"]["on_leave"] == 3
    assert "absent" not in summary["counts"]


def test_leave_over_weekends_and_holidays(client, hr, make_employee, db_session, types):
    user, _ = make_employee()
    db_session.add(Holiday(name="Special Day", date=date(2026, 11, 10)))
    db_session.commit()
    leave = apply(client, user, types["Annual"], "2026-11-06", "2026-11-11").json()  # Fri to Wed
    assert leave["days"] == 3
    approve(client, hr, leave["id"])

    days, summary = month_days(client, user)
    assert days["2026-11-06"] == "on_leave"  # leave beats "upcoming"
    assert days["2026-11-07"] == "weekend"
    assert days["2026-11-08"] == "weekend"
    assert days["2026-11-09"] == "on_leave"
    assert days["2026-11-10"] == "holiday"
    assert days["2026-11-11"] == "on_leave"
    assert days["2026-11-12"] == "upcoming"
    assert summary["counts"]["on_leave"] == 3


def test_a_real_check_in_wins_over_leave(client, hr, make_employee, db_session, types):
    user, emp = make_employee()
    leave = apply(client, user, types["Annual"], "2026-11-02", "2026-11-04").json()
    approve(client, hr, leave["id"])
    add_record(db_session, emp, date(2026, 11, 3), DayStatus.present)

    days, summary = month_days(client, user)
    assert days["2026-11-02"] == "on_leave"
    assert days["2026-11-03"] == "present"
    assert days["2026-11-04"] == "on_leave"
    assert summary["counts"]["on_leave"] == 2


def test_cancelled_leave_stops_showing(client, hr, make_employee, types):
    user, _ = make_employee()
    leave = apply(client, user, types["Annual"], "2026-11-06", "2026-11-06").json()
    approve(client, hr, leave["id"])
    assert month_days(client, user)[0]["2026-11-06"] == "on_leave"
    cancel(client, user, leave["id"])
    assert month_days(client, user)[0]["2026-11-06"] == "upcoming"


def test_board_shows_who_is_on_leave(client, hr, make_employee, types):
    ann, _ = make_employee(email="a@t.com", name="Ann")
    make_employee(email="b@t.com", name="Bob")
    leave = apply(client, ann, types["Annual"], "2026-11-04", "2026-11-05").json()  # yesterday and today
    approve(client, hr, leave["id"])

    today = client.get("/attendance/board", headers=hr).json()
    assert {i["full_name"]: i["status"] for i in today["items"]} == {"Ann": "on_leave", "Bob": "not_checked_in"}
    assert today["summary"] == {"on_leave": 1, "not_checked_in": 1}

    yesterday = client.get("/attendance/board?date=2026-11-04", headers=hr).json()
    assert {i["full_name"]: i["status"] for i in yesterday["items"]} == {"Ann": "on_leave", "Bob": "absent"}

    only = client.get("/attendance/board?status=on_leave", headers=hr).json()
    assert only["total"] == 1
    assert only["items"][0]["full_name"] == "Ann"