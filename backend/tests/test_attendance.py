from datetime import date, datetime, time, timezone

import pytest

from app.core import clock
from app.core.security import create_access_token
from app.models.attendance import Attendance, DayStatus
from app.models.company import Company
from app.models.employee import Employee, EmployeeStatus
from app.models.leave_config import Holiday
from app.models.org import Department, Designation
from app.models.user import Role
from app.services import calendar_service, settings_service

# 2026-11-04 is a Wednesday, 2026-11-05 a Thursday, 2026-11-01 a Sunday


def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value)}"}


def set_now(monkeypatch, text):
    moment = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=clock.office_tz())
    monkeypatch.setattr(clock, "now", lambda: moment)


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


def add_record(db, emp, day, status, is_late=False, closed=True):
    db.add(Attendance(
        employee_id=emp.id,
        date=day,
        check_in=datetime(day.year, day.month, day.day, 4, 0, tzinfo=timezone.utc),  # 09:00 office time
        check_out=datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc) if closed else None,
        status=status,
        is_late=is_late,
        source="web",
    ))
    db.commit()


# ---------- Check-in ----------

def test_check_in_is_stamped_by_the_server(client, make_employee, monkeypatch):
    user, _ = make_employee()
    set_now(monkeypatch, "2026-11-04 09:10")
    r = client.post(
        "/attendance/check-in",
        json={"check_in": "2020-01-01T00:00:00"},  # ignored: the client cannot choose the time
        headers=auth(user),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["date"] == "2026-11-04"
    assert body["status"] == "present"
    assert body["is_late"] is False
    assert body["check_in"].startswith("2026-11-04T09:10:00")
    assert body["check_in"].endswith("+05:00")
    assert body["check_out"] is None
    assert body["source"] == "web"


@pytest.mark.parametrize(
    "clock_text,expected",
    [
        ("2026-11-04 08:30", "present"),
        ("2026-11-04 09:00", "present"),
        ("2026-11-04 09:15", "present"),  # exactly at the end of the grace time
        ("2026-11-04 09:16", "late"),
        ("2026-11-04 12:30", "late"),
    ],
)
def test_late_rule(client, make_employee, monkeypatch, clock_text, expected):
    user, _ = make_employee()
    set_now(monkeypatch, clock_text)
    r = client.post("/attendance/check-in", headers=auth(user))
    assert r.json()["status"] == expected
    assert r.json()["is_late"] == (expected == "late")


def test_check_in_uses_company_settings(client, make_employee, db_session, monkeypatch):
    on_time, _ = make_employee(email="a@t.com", name="Ann")
    late, _ = make_employee(email="b@t.com", name="Bob")
    cfg = settings_service.get_settings(db_session)
    cfg.office_start = time(10, 0)
    cfg.grace_minutes = 0
    db_session.commit()

    set_now(monkeypatch, "2026-11-04 10:00")
    assert client.post("/attendance/check-in", headers=auth(on_time)).json()["status"] == "present"
    set_now(monkeypatch, "2026-11-04 10:01")
    assert client.post("/attendance/check-in", headers=auth(late)).json()["status"] == "late"


def test_second_check_in_same_day_is_409(client, make_employee, db_session, monkeypatch):
    user, emp = make_employee()
    set_now(monkeypatch, "2026-11-04 09:00")
    assert client.post("/attendance/check-in", headers=auth(user)).status_code == 201
    set_now(monkeypatch, "2026-11-04 15:00")
    assert client.post("/attendance/check-in", headers=auth(user)).status_code == 409
    assert db_session.query(Attendance).filter(Attendance.employee_id == emp.id).count() == 1


def test_next_day_gets_a_new_record(client, make_employee, monkeypatch):
    user, _ = make_employee()
    set_now(monkeypatch, "2026-11-04 09:00")
    client.post("/attendance/check-in", headers=auth(user))
    set_now(monkeypatch, "2026-11-05 09:00")
    r = client.post("/attendance/check-in", headers=auth(user))
    assert r.status_code == 201
    assert r.json()["date"] == "2026-11-05"


def test_weekend_check_in_is_allowed(client, make_employee, monkeypatch):
    user, _ = make_employee()
    set_now(monkeypatch, "2026-10-31 09:00")  # a Saturday
    r = client.post("/attendance/check-in", headers=auth(user))
    assert r.status_code == 201
    assert r.json()["status"] == "present"


def test_cannot_check_in_before_joining_date(client, make_employee, monkeypatch):
    user, _ = make_employee(join=date(2026, 12, 1))
    set_now(monkeypatch, "2026-11-04 09:00")
    assert client.post("/attendance/check-in", headers=auth(user)).status_code == 409
    assert client.get("/attendance/today", headers=auth(user)).status_code == 409


# ---------- Check-out and half day ----------

@pytest.mark.parametrize(
    "in_at,out_at,status,hours,is_late",
    [
        ("09:00", "17:00", "present", 8.0, False),
        ("09:00", "14:00", "present", 5.0, False),   # exactly 5 hours is a full day
        ("09:00", "13:59", "half_day", 4.98, False),
        ("09:00", "12:00", "half_day", 3.0, False),
        ("09:30", "17:30", "late", 8.0, True),
        ("09:30", "12:00", "half_day", 2.5, True),   # late AND short: half day, still counted as late
    ],
)
def test_check_out_decides_the_final_status(client, make_employee, monkeypatch, in_at, out_at, status, hours, is_late):
    user, _ = make_employee()
    set_now(monkeypatch, f"2026-11-04 {in_at}")
    client.post("/attendance/check-in", headers=auth(user))
    set_now(monkeypatch, f"2026-11-04 {out_at}")
    r = client.post("/attendance/check-out", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == status
    assert body["worked_hours"] == hours
    assert body["is_late"] is is_late
    assert body["check_out"].startswith(f"2026-11-04T{out_at}:00")


def test_check_out_rules(client, make_employee, monkeypatch):
    user, _ = make_employee()
    set_now(monkeypatch, "2026-11-04 17:00")
    assert client.post("/attendance/check-out", headers=auth(user)).status_code == 409  # never checked in

    set_now(monkeypatch, "2026-11-04 09:00")
    client.post("/attendance/check-in", headers=auth(user))
    set_now(monkeypatch, "2026-11-04 17:00")
    assert client.post("/attendance/check-out", headers=auth(user)).status_code == 200
    assert client.post("/attendance/check-out", headers=auth(user)).status_code == 409  # twice


def test_forgotten_checkout_cannot_be_fixed_the_next_day(client, make_employee, monkeypatch):
    user, _ = make_employee()
    set_now(monkeypatch, "2026-11-04 09:00")
    client.post("/attendance/check-in", headers=auth(user))
    set_now(monkeypatch, "2026-11-05 08:00")
    assert client.post("/attendance/check-out", headers=auth(user)).status_code == 409


# ---------- Today ----------

def test_today_widget(client, make_employee, monkeypatch):
    user, _ = make_employee()
    set_now(monkeypatch, "2026-11-04 08:00")
    assert client.get("/attendance/today", headers=auth(user)).json()["status"] == "not_checked_in"

    set_now(monkeypatch, "2026-11-04 09:00")
    client.post("/attendance/check-in", headers=auth(user))
    body = client.get("/attendance/today", headers=auth(user)).json()
    assert body["status"] == "present"
    assert body["check_out"] is None


# ---------- Who may use it ----------

def test_only_employees_check_in_and_out(client, hr, admin, make_user, org):
    no_profile = auth(make_user(email="x@t.com", role=Role.employee))  # an employee login with no employee record
    for path in ("/attendance/check-in", "/attendance/check-out"):
        assert client.post(path, headers=hr).status_code == 403
        assert client.post(path, headers=admin).status_code == 403
        assert client.post(path).status_code == 401
        assert client.post(path, headers=no_profile).status_code == 404
    assert client.get("/attendance/today", headers=hr).status_code == 403
    assert client.get("/attendance/me", headers=admin).status_code == 403


# ---------- Calendar ----------

def test_calendar_statuses(client, make_employee, db_session, monkeypatch):
    user, emp = make_employee(join=date(2026, 10, 28))
    add_record(db_session, emp, date(2026, 11, 2), DayStatus.present)
    add_record(db_session, emp, date(2026, 11, 3), DayStatus.late, is_late=True, closed=False)
    db_session.add(Holiday(name="Special Day", date=date(2026, 11, 4)))
    db_session.commit()
    set_now(monkeypatch, "2026-11-05 10:00")

    r = client.get("/attendance/me?month=2026-11", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    status = {d["date"]: d["status"] for d in body["days"]}
    assert body["month"] == "2026-11"
    assert len(body["days"]) == 30
    assert status["2026-11-01"] == "weekend"          # Sunday
    assert status["2026-11-02"] == "present"
    assert status["2026-11-03"] == "missing_checkout"  # past day, never checked out
    assert status["2026-11-04"] == "holiday"
    assert status["2026-11-05"] == "not_checked_in"    # today, the day is not over
    assert status["2026-11-06"] == "upcoming"
    assert status["2026-11-07"] == "weekend"

    counts = body["summary"]["counts"]
    assert counts["present"] == 1
    assert counts["missing_checkout"] == 1
    assert counts["holiday"] == 1
    assert counts.get("absent", 0) == 0
    assert "upcoming" not in counts
    assert body["summary"]["late_count"] == 1
    assert body["summary"]["total_hours"] == 8.0


def test_absent_means_a_working_day_with_no_record(client, make_employee, monkeypatch):
    user, _ = make_employee(join=date(2026, 11, 2))
    set_now(monkeypatch, "2026-11-05 10:00")
    body = client.get("/attendance/me?month=2026-11", headers=auth(user)).json()
    status = {d["date"]: d["status"] for d in body["days"]}
    assert body["days"][0]["date"] == "2026-11-02"  # nothing before the joining date
    assert status["2026-11-02"] == "absent"
    assert status["2026-11-03"] == "absent"
    assert status["2026-11-04"] == "absent"
    assert status["2026-11-05"] == "not_checked_in"
    assert body["summary"]["counts"]["absent"] == 3


def test_calendar_stops_at_exit_date(client, hr, make_employee, monkeypatch):
    _, emp = make_employee(join=date(2026, 10, 1), exit_date=date(2026, 11, 3))
    set_now(monkeypatch, "2026-11-10 10:00")
    body = client.get(f"/attendance/employees/{emp.id}?month=2026-11", headers=hr).json()
    assert body["days"][-1]["date"] == "2026-11-03"


def test_calendar_defaults_to_this_month_and_checks_the_month(client, make_employee, monkeypatch):
    user, _ = make_employee()
    set_now(monkeypatch, "2026-11-05 10:00")
    assert client.get("/attendance/me", headers=auth(user)).json()["month"] == "2026-11"
    assert client.get("/attendance/me?month=2026-13", headers=auth(user)).status_code == 422
    assert client.get("/attendance/me?month=nonsense", headers=auth(user)).status_code == 422


def test_staff_can_open_one_employee_calendar(client, hr, admin, make_employee, monkeypatch):
    user, emp = make_employee()
    set_now(monkeypatch, "2026-11-05 10:00")
    for headers in (hr, admin):
        r = client.get(f"/attendance/employees/{emp.id}?month=2026-11", headers=headers)
        assert r.status_code == 200
        assert r.json()["employee"]["full_name"] == "Ali Khan"
    assert client.get("/attendance/employees/999", headers=hr).status_code == 404
    assert client.get(f"/attendance/employees/{emp.id}", headers=auth(user)).status_code == 403
    assert client.get(f"/attendance/employees/{emp.id}").status_code == 401


# ---------- Daily board ----------

def test_board_summary_filters_and_paging(client, hr, make_employee, db_session, monkeypatch):
    sales = Department(name="Sales")
    db_session.add(sales)
    db_session.commit()
    ann, _ = make_employee(email="a@t.com", name="Ann")
    bob, _ = make_employee(email="b@t.com", name="Bob")
    make_employee(email="c@t.com", name="Cid", department_id=sales.id)

    set_now(monkeypatch, "2026-11-04 09:00")
    client.post("/attendance/check-in", headers=auth(ann))
    set_now(monkeypatch, "2026-11-04 09:30")
    client.post("/attendance/check-in", headers=auth(bob))
    set_now(monkeypatch, "2026-11-04 11:00")

    body = client.get("/attendance/board", headers=hr).json()
    assert body["date"] == "2026-11-04"
    assert body["total"] == 3
    assert body["summary"] == {"present": 1, "late": 1, "not_checked_in": 1}
    assert {i["full_name"]: i["status"] for i in body["items"]} == {
        "Ann": "present", "Bob": "late", "Cid": "not_checked_in",
    }
    assert body["items"][0]["department"]["name"] == "Engineering"

    def total(query):
        return client.get(f"/attendance/board?{query}", headers=hr).json()["total"]

    assert total("status=late") == 1
    assert total("status=not_checked_in") == 1
    assert total("search=cid") == 1
    assert total(f"department_id={sales.id}") == 1

    page = client.get("/attendance/board?limit=2&offset=2", headers=hr).json()
    assert len(page["items"]) == 1
    assert page["total"] == 3
    assert page["summary"] == {"present": 1, "late": 1, "not_checked_in": 1}  # summary covers everyone

    assert client.get("/attendance/board?status=bogus", headers=hr).status_code == 422
    assert client.get("/attendance/board?limit=0", headers=hr).status_code == 422


def test_board_for_a_past_day(client, hr, make_employee, db_session, monkeypatch):
    _, ann = make_employee(email="a@t.com", name="Ann")
    make_employee(email="b@t.com", name="Bob")
    add_record(db_session, ann, date(2026, 11, 3), DayStatus.present, closed=False)  # forgot to check out
    set_now(monkeypatch, "2026-11-04 11:00")

    body = client.get("/attendance/board?date=2026-11-03", headers=hr).json()
    assert {i["full_name"]: i["status"] for i in body["items"]} == {
        "Ann": "missing_checkout", "Bob": "absent",
    }


def test_board_rejects_future_dates_and_bad_dates(client, hr, make_employee, monkeypatch):
    make_employee()
    set_now(monkeypatch, "2026-11-04 11:00")
    assert client.get("/attendance/board?date=2026-11-10", headers=hr).status_code == 400
    assert client.get("/attendance/board?date=garbage", headers=hr).status_code == 422


def test_board_only_lists_people_employed_that_day(client, hr, make_employee, monkeypatch):
    make_employee(email="a@t.com", name="Ann")
    make_employee(email="n@t.com", name="New Joiner", join=date(2026, 11, 4))
    make_employee(email="g@t.com", name="Gone", join=date(2026, 1, 1), exit_date=date(2026, 11, 2))
    set_now(monkeypatch, "2026-11-04 11:00")

    def names(day):
        items = client.get(f"/attendance/board?date={day}", headers=hr).json()["items"]
        return {i["full_name"] for i in items}

    assert names("2026-11-02") == {"Ann", "Gone"}        # Gone's last day counts
    assert names("2026-11-03") == {"Ann"}                # the day after they left
    assert names("2026-11-04") == {"Ann", "New Joiner"}  # New Joiner starts today

def test_board_on_a_weekend(client, hr, make_employee, monkeypatch):
    make_employee()
    set_now(monkeypatch, "2026-11-04 11:00")
    body = client.get("/attendance/board?date=2026-11-01", headers=hr).json()  # a Sunday
    assert body["summary"] == {"weekend": 1}


def test_board_permissions(client, admin, hr, make_employee):
    user, _ = make_employee()
    assert client.get("/attendance/board", headers=hr).status_code == 200
    assert client.get("/attendance/board", headers=admin).status_code == 200
    assert client.get("/attendance/board", headers=auth(user)).status_code == 403
    assert client.get("/attendance/board").status_code == 401


# ---------- Working-day helper (also used by leave and payroll later) ----------

def test_working_day_helpers():
    weekend = [5, 6]
    holidays = {date(2026, 11, 4)}
    assert calendar_service.is_working_day(date(2026, 11, 3), weekend, holidays)
    assert not calendar_service.is_working_day(date(2026, 11, 1), weekend, holidays)
    assert not calendar_service.is_working_day(date(2026, 11, 4), weekend, holidays)
    # Monday 2 to Sunday 8 November
    assert calendar_service.count_working_days(date(2026, 11, 2), date(2026, 11, 8), weekend, set()) == 5
    assert calendar_service.count_working_days(date(2026, 11, 2), date(2026, 11, 8), weekend, holidays) == 4
    assert calendar_service.count_working_days(date(2026, 11, 8), date(2026, 11, 2), weekend, set()) == 0
    assert calendar_service.count_working_days(date(2026, 11, 2), date(2026, 11, 2), [6], set()) == 1