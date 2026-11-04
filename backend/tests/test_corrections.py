from datetime import date, datetime, timezone

import pytest

from app.core import clock
from app.core.security import create_access_token
from app.models.attendance import Attendance, DayStatus
from app.models.company import Company
from app.models.employee import Employee, EmployeeStatus
from app.models.org import Department, Designation
from app.models.user import Role

# In these tests "now" is Thursday 2026-11-05, 10:00 office time (the 30-day window starts 2026-10-06).


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
def make_employee(db_session, make_user, org):
    counter = {"n": 0}

    def _make(email="emp@t.com", name="Ali Khan", join=date(2026, 1, 1), exit_date=None):
        counter["n"] += 1
        user = make_user(email=email, role=Role.employee)
        emp = Employee(
            user_id=user.id,
            employee_code=f"EMP-{counter['n']:04d}",
            full_name=name,
            department_id=org["dept"],
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


def add_record(db, emp, day, status, closed=True):
    """A record from 09:00 to 17:00 office time (04:00 to 12:00 UTC)."""
    db.add(Attendance(
        employee_id=emp.id,
        date=day,
        check_in=datetime(day.year, day.month, day.day, 4, 0, tzinfo=timezone.utc),
        check_out=datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc) if closed else None,
        status=status,
        is_late=False,
        source="web",
    ))
    db.commit()


def body(**over):
    data = {"date": "2026-11-04", "check_in": "09:00", "check_out": "17:00", "reason": "Forgot to check in"}
    data.update(over)
    return data


def request(client, user, **over):
    return client.post("/attendance/corrections", json=body(**over), headers=auth(user))


def approve(client, hr, correction_id, comment=None):
    payload = {"comment": comment} if comment else None
    return client.post(f"/attendance/corrections/{correction_id}/approve", json=payload, headers=hr)


def day_of(client, user, day):
    days = client.get(f"/attendance/me?month={day[:7]}", headers=auth(user)).json()["days"]
    return next(d for d in days if d["date"] == day)


# ---------- Requesting ----------

def test_request_for_a_missed_day(client, make_employee):
    user, _ = make_employee()
    r = request(client, user)
    assert r.status_code == 201
    out = r.json()
    assert out["status"] == "pending"
    assert out["date"] == "2026-11-04"
    assert out["requested_check_in"].startswith("2026-11-04T09:00:00")
    assert out["requested_check_in"].endswith("+05:00")
    assert out["requested_check_out"].startswith("2026-11-04T17:00:00")
    assert out["reason"] == "Forgot to check in"
    assert out["employee"]["full_name"] == "Ali Khan"
    assert out["reviewed_by"] is None
    assert out["review_comment"] is None
    assert out["current"]["status"] == "absent"
    # Asking for a correction changes nothing by itself
    assert day_of(client, user, "2026-11-04")["status"] == "absent"


def test_reason_is_cleaned(client, make_employee):
    user, _ = make_employee()
    assert request(client, user, reason="  Forgot   to   check in  ").json()["reason"] == "Forgot to check in"


def test_the_30_day_window(client, make_employee):
    user, _ = make_employee()
    assert request(client, user, date="2026-10-06").status_code == 201  # exactly 30 days ago
    assert request(client, user, date="2026-10-05").status_code == 400


def test_dates_outside_employment_or_in_the_future(client, make_employee):
    new_joiner, _ = make_employee(email="n@t.com", name="New", join=date(2026, 11, 3))
    assert request(client, new_joiner, date="2026-11-02").status_code == 400
    assert request(client, new_joiner, date="2026-11-03").status_code == 201

    leaver, _ = make_employee(email="l@t.com", name="Lee", exit_date=date(2026, 11, 2))
    assert request(client, leaver, date="2026-11-03").status_code == 400
    assert request(client, leaver, date="2026-11-02").status_code == 201

    someone, _ = make_employee(email="s@t.com", name="Sam")
    assert request(client, someone, date="2026-11-06").status_code == 400  # tomorrow


@pytest.mark.parametrize(
    "over",
    [
        {"check_in": "17:00", "check_out": "09:00"},
        {"check_in": "09:00", "check_out": "09:00"},
        {"check_in": None, "check_out": "17:00"},  # no record for that day, so both times are needed
        {"check_in": "09:00", "check_out": None},
    ],
)
def test_bad_times_are_400(client, make_employee, over):
    user, _ = make_employee()
    assert request(client, user, **over).status_code == 400


def test_times_in_the_future_are_rejected(client, make_employee):
    user, _ = make_employee()
    assert request(client, user, date="2026-11-05", check_in="09:00", check_out="17:00").status_code == 400
    assert request(client, user, date="2026-11-05", check_in="09:00", check_out="09:30").status_code == 201


def test_a_new_time_must_fit_the_existing_record(client, make_employee, db_session):
    user, emp = make_employee()
    add_record(db_session, emp, date(2026, 11, 3), DayStatus.present)  # 09:00 to 17:00
    assert request(client, user, date="2026-11-03", check_in=None, check_out="08:00").status_code == 400
    assert request(client, user, date="2026-11-03", check_in="18:00", check_out=None).status_code == 400
    assert request(client, user, date="2026-11-03", check_in=None, check_out="16:00").status_code == 201


@pytest.mark.parametrize(
    "over",
    [
        {"check_in": None, "check_out": None},
        {"reason": "abc"},
        {"reason": "      "},
        {"reason": "x" * 501},
        {"check_in": "25:00"},
        {"date": "garbage"},
    ],
)
def test_bad_input_is_422(client, make_employee, over):
    user, _ = make_employee()
    assert request(client, user, **over).status_code == 422


def test_missing_fields_are_422(client, make_employee):
    user, _ = make_employee()
    no_date = {"check_in": "09:00", "reason": "Forgot to check in"}
    no_reason = {"date": "2026-11-04", "check_in": "09:00"}
    assert client.post("/attendance/corrections", json=no_date, headers=auth(user)).status_code == 422
    assert client.post("/attendance/corrections", json=no_reason, headers=auth(user)).status_code == 422


def test_one_pending_request_per_day(client, hr, make_employee):
    user, _ = make_employee()
    first = request(client, user).json()
    assert request(client, user).status_code == 409
    assert request(client, user, date="2026-11-03").status_code == 201  # another day is fine

    rejected = client.post(
        f"/attendance/corrections/{first['id']}/reject", json={"comment": "Not convincing"}, headers=hr
    )
    assert rejected.status_code == 200
    assert request(client, user).status_code == 201  # after a decision a new request is allowed


# ---------- Approving ----------

@pytest.mark.parametrize(
    "check_in,check_out,status,is_late",
    [
        ("09:00", "17:00", "present", False),
        ("09:15", "17:00", "present", False),
        ("09:16", "17:00", "late", True),
        ("09:30", "17:00", "late", True),
        ("09:00", "12:00", "half_day", False),
    ],
)
def test_approval_applies_the_attendance_rules(client, hr, make_employee, check_in, check_out, status, is_late):
    user, _ = make_employee()
    created = request(client, user, check_in=check_in, check_out=check_out).json()

    r = approve(client, hr, created["id"], "Checked the gate log")
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "approved"
    assert out["review_comment"] == "Checked the gate log"
    assert out["reviewed_by"] == "hr@t.com"
    assert out["reviewed_at"] is not None

    day = day_of(client, user, "2026-11-04")
    assert day["status"] == status
    assert day["is_late"] is is_late
    assert day["source"] == "correction"


def test_approved_day_shows_everywhere(client, hr, make_employee):
    user, _ = make_employee()
    created = request(client, user).json()
    assert approve(client, hr, created["id"]).status_code == 200  # the comment is optional

    day = day_of(client, user, "2026-11-04")
    assert day["worked_hours"] == 8.0
    assert day["check_in"].startswith("2026-11-04T09:00:00")
    assert day["check_out"].startswith("2026-11-04T17:00:00")

    board = client.get("/attendance/board?date=2026-11-04", headers=hr).json()
    assert {i["full_name"]: i["status"] for i in board["items"]} == {"Ali Khan": "present"}


def test_fixing_a_forgotten_check_out(client, hr, make_employee, db_session):
    user, emp = make_employee()
    add_record(db_session, emp, date(2026, 11, 3), DayStatus.present, closed=False)
    assert day_of(client, user, "2026-11-03")["status"] == "missing_checkout"

    created = request(client, user, date="2026-11-03", check_in=None, check_out="17:00").json()
    assert created["current"]["status"] == "missing_checkout"
    approve(client, hr, created["id"])

    day = day_of(client, user, "2026-11-03")
    assert day["status"] == "present"
    assert day["worked_hours"] == 8.0
    assert day["check_in"].startswith("2026-11-03T09:00:00")  # kept as it was
    assert day["check_out"].startswith("2026-11-03T17:00:00")


def test_an_earlier_check_out_recalculates_to_half_day(client, hr, make_employee, db_session):
    user, emp = make_employee()
    add_record(db_session, emp, date(2026, 11, 3), DayStatus.present)
    created = request(client, user, date="2026-11-03", check_in=None, check_out="12:00").json()
    approve(client, hr, created["id"])

    day = day_of(client, user, "2026-11-03")
    assert day["status"] == "half_day"
    assert day["worked_hours"] == 3.0
    assert day["check_in"].startswith("2026-11-03T09:00:00")


def test_a_later_check_in_makes_the_day_late(client, hr, make_employee, db_session):
    user, emp = make_employee()
    add_record(db_session, emp, date(2026, 11, 3), DayStatus.present)
    created = request(client, user, date="2026-11-03", check_in="10:00", check_out=None).json()
    approve(client, hr, created["id"])

    day = day_of(client, user, "2026-11-03")
    assert day["status"] == "late"
    assert day["is_late"] is True
    assert day["worked_hours"] == 7.0
    assert day["check_out"].startswith("2026-11-03T17:00:00")  # kept as it was


def test_approval_fails_if_the_record_changed_meanwhile(client, hr, make_employee, db_session):
    user, emp = make_employee()
    add_record(db_session, emp, date(2026, 11, 3), DayStatus.present)
    created = request(client, user, date="2026-11-03", check_in=None, check_out="10:00").json()

    record = db_session.query(Attendance).filter(Attendance.employee_id == emp.id).one()
    record.check_in = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)  # now 11:00, after the requested check-out
    db_session.commit()

    assert approve(client, hr, created["id"]).status_code == 409
    pending = client.get("/attendance/corrections?status=pending", headers=hr).json()
    assert pending["total"] == 1  # it stays pending


# ---------- Rejecting and cancelling ----------

def test_rejection_needs_a_comment_and_changes_nothing(client, hr, make_employee):
    user, _ = make_employee()
    path = f"/attendance/corrections/{request(client, user).json()['id']}"

    for bad in ({}, {"comment": ""}, {"comment": "  "}, {"comment": "no"}):
        assert client.post(f"{path}/reject", json=bad, headers=hr).status_code == 422

    r = client.post(f"{path}/reject", json={"comment": "No gate log for that day"}, headers=hr)
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["review_comment"] == "No gate log for that day"
    assert r.json()["reviewed_by"] == "hr@t.com"
    assert day_of(client, user, "2026-11-04")["status"] == "absent"


def test_decisions_are_final(client, hr, make_employee):
    user, _ = make_employee()
    path = f"/attendance/corrections/{request(client, user).json()['id']}"

    assert client.post(f"{path}/approve", headers=hr).status_code == 200  # no body at all is fine
    assert client.post(f"{path}/approve", headers=hr).status_code == 409
    assert client.post(f"{path}/reject", json={"comment": "Too late"}, headers=hr).status_code == 409
    assert client.post(f"{path}/cancel", headers=auth(user)).status_code == 409

    assert client.post("/attendance/corrections/999/approve", headers=hr).status_code == 404
    assert client.post("/attendance/corrections/999/reject", json={"comment": "No way"}, headers=hr).status_code == 404
    assert client.post("/attendance/corrections/999/cancel", headers=auth(user)).status_code == 404


def test_employee_can_cancel_a_pending_request(client, hr, make_employee):
    user, _ = make_employee()
    path = f"/attendance/corrections/{request(client, user).json()['id']}"

    r = client.post(f"{path}/cancel", headers=auth(user))
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert client.post(f"{path}/approve", headers=hr).status_code == 409
    assert client.post(f"{path}/cancel", headers=auth(user)).status_code == 409
    assert request(client, user).status_code == 201  # the day is free again


# ---------- Who can do what ----------

def test_who_can_do_what(client, hr, admin, make_employee, make_user):
    user, _ = make_employee()
    other, _ = make_employee(email="o@t.com", name="Other")
    no_profile = auth(make_user(email="x@t.com", role=Role.employee))  # a login with no employee record
    path = f"/attendance/corrections/{request(client, user).json()['id']}"

    # Only employees ask
    for headers in (hr, admin):
        assert client.post("/attendance/corrections", json=body(), headers=headers).status_code == 403
        assert client.get("/attendance/corrections/me", headers=headers).status_code == 403
    assert client.post("/attendance/corrections", json=body()).status_code == 401
    assert client.post("/attendance/corrections", json=body(), headers=no_profile).status_code == 404

    # Only HR decides
    for headers in (admin, auth(user)):
        assert client.post(f"{path}/approve", headers=headers).status_code == 403
        assert client.post(f"{path}/reject", json={"comment": "No way"}, headers=headers).status_code == 403
    assert client.post(f"{path}/approve").status_code == 401

    # HR and Admin read the inbox, employees cannot
    assert client.get("/attendance/corrections", headers=hr).status_code == 200
    assert client.get("/attendance/corrections", headers=admin).status_code == 200
    assert client.get("/attendance/corrections", headers=auth(user)).status_code == 403
    assert client.get("/attendance/corrections").status_code == 401

    # Only the owner cancels
    assert client.post(f"{path}/cancel", headers=auth(other)).status_code == 404
    assert client.post(f"{path}/cancel", headers=hr).status_code == 403
    assert client.post(f"{path}/cancel", headers=auth(user)).status_code == 200


# ---------- Lists ----------

def test_lists_filters_and_paging(client, hr, admin, make_employee):
    ann, _ = make_employee(email="a@t.com", name="Ann")
    bob, _ = make_employee(email="b@t.com", name="Bob")
    a1 = request(client, ann, date="2026-11-04").json()
    a2 = request(client, ann, date="2026-11-03").json()
    b1 = request(client, bob, date="2026-11-04").json()
    client.post(f"/attendance/corrections/{a2['id']}/reject", json={"comment": "Not allowed"}, headers=hr)

    mine = client.get("/attendance/corrections/me", headers=auth(ann)).json()
    assert mine["total"] == 2
    assert {i["employee"]["full_name"] for i in mine["items"]} == {"Ann"}  # never anyone else's
    assert [i["id"] for i in mine["items"]] == [a2["id"], a1["id"]]  # newest first
    assert client.get("/attendance/corrections/me?status=pending", headers=auth(ann)).json()["total"] == 1

    everything = client.get("/attendance/corrections", headers=hr).json()
    assert everything["total"] == 3
    assert everything["items"][0]["id"] == b1["id"]
    assert everything["items"][0]["current"]["status"] == "absent"
    assert client.get("/attendance/corrections", headers=admin).json()["total"] == 3

    def total(query):
        return client.get(f"/attendance/corrections?{query}", headers=hr).json()["total"]

    assert total("status=pending") == 2
    assert total("status=rejected") == 1
    assert total(f"employee_id={bob_id(b1)}") == 1

    page1 = client.get("/attendance/corrections?limit=2&offset=0", headers=hr).json()
    page2 = client.get("/attendance/corrections?limit=2&offset=2", headers=hr).json()
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1
    assert page1["total"] == 3

    for bad in ("status=bogus", "limit=0", "limit=1000", "offset=-1"):
        assert client.get(f"/attendance/corrections?{bad}", headers=hr).status_code == 422


def bob_id(correction):
    return correction["employee"]["id"]