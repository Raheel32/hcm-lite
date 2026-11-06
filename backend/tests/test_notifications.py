from datetime import date, datetime, timezone

import pytest

from app.core import clock
from app.core.security import create_access_token
from app.models.comms import Notification
from app.models.company import Company
from app.models.employee import Employee, EmployeeStatus
from app.models.leave_config import LeaveType
from app.models.org import Department, Designation
from app.models.user import Role


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
def annual(db_session, company):
    leave_type = LeaveType(name="Annual", yearly_quota=14, is_paid=True)
    db_session.add(leave_type)
    db_session.commit()
    return leave_type.id


@pytest.fixture()
def make_employee(db_session, make_user, org):
    counter = {"n": 0}

    def _make(email="emp@t.com", name="Ali Khan", join=date(2025, 1, 1)):
        counter["n"] += 1
        user = make_user(email=email, role=Role.employee)
        emp = Employee(
            user_id=user.id, employee_code=f"EMP-{counter['n']:04d}", full_name=name,
            department_id=org["dept"], designation_id=org["desig"], join_date=join,
            status=EmployeeStatus.active, basic_salary=50000,
        )
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        return user, emp

    return _make


def notes(client, headers, query=""):
    r = client.get(f"/notifications{query}", headers=headers)
    assert r.status_code == 200
    return r.json()


def apply_leave(client, user, leave_type_id, start="2026-11-09", end="2026-11-10"):
    body = {"leave_type_id": leave_type_id, "start_date": start, "end_date": end, "reason": "Family event"}
    r = client.post("/leave", json=body, headers=auth(user))
    assert r.status_code == 201
    return r.json()


# ---------- Who gets told what ----------

def test_a_leave_request_notifies_active_hr_only(client, make_user, make_employee, db_session, annual):
    hr1 = auth(make_user(email="hr1@t.com", role=Role.hr))
    hr2 = auth(make_user(email="hr2@t.com", role=Role.hr))
    gone = make_user(email="gone@t.com", role=Role.hr, is_active=False)
    admin = auth(make_user(email="admin@t.com", role=Role.admin))
    user, _ = make_employee()

    leave = apply_leave(client, user, annual)

    for hr in (hr1, hr2):
        data = notes(client, hr)
        assert data["total"] == 1 and data["unread"] == 1
        item = data["items"][0]
        assert item["kind"] == "leave.submitted"
        assert "Ali Khan" in item["body"] and "2026-11-09" in item["body"]
        assert item["entity_type"] == "leave_request" and item["entity_id"] == leave["id"]
        assert item["is_read"] is False and item["read_at"] is None
    assert notes(client, admin)["total"] == 0
    assert notes(client, auth(user))["total"] == 0
    assert db_session.query(Notification).filter(Notification.user_id == gone.id).count() == 0


def test_leave_decisions_notify_the_employee(client, make_user, make_employee, annual):
    hr = auth(make_user(email="hr@t.com", role=Role.hr))
    user, _ = make_employee()
    first = apply_leave(client, user, annual, "2026-11-09", "2026-11-10")
    second = apply_leave(client, user, annual, "2026-11-16", "2026-11-17")

    assert client.post(f"/leave/{first['id']}/approve", json={"comment": "Enjoy"}, headers=hr).status_code == 200
    assert client.post(f"/leave/{second['id']}/reject", json={"comment": "Team is short"}, headers=hr).status_code == 200

    items = notes(client, auth(user))["items"]
    assert [i["kind"] for i in items] == ["leave.rejected", "leave.approved"]
    assert "rejected" in items[0]["body"] and "Team is short" in items[0]["body"]
    assert "approved" in items[1]["body"] and "Enjoy" in items[1]["body"]
    assert items[1]["entity_id"] == first["id"]
    assert notes(client, hr)["total"] == 2  # HR only got the two "new request" notices, nothing about their own decisions


def test_corrections_notify_both_ways(client, make_user, make_employee):
    hr = auth(make_user(email="hr@t.com", role=Role.hr))
    user, _ = make_employee()

    def request(day):
        body = {"date": day, "check_in": "09:00", "check_out": "17:00", "reason": "Forgot to check in"}
        r = client.post("/attendance/corrections", json=body, headers=auth(user))
        assert r.status_code == 201
        return r.json()

    c1, c2 = request("2026-11-04"), request("2026-11-03")
    hr_items = notes(client, hr)["items"]
    assert [i["kind"] for i in hr_items] == ["correction.submitted", "correction.submitted"]
    assert "2026-11-04" in hr_items[1]["body"] and "Ali Khan" in hr_items[1]["body"]

    assert client.post(f"/attendance/corrections/{c1['id']}/approve", json={"comment": "Checked"}, headers=hr).status_code == 200
    assert client.post(f"/attendance/corrections/{c2['id']}/reject", json={"comment": "No gate log"}, headers=hr).status_code == 200

    mine = notes(client, auth(user))["items"]
    assert [i["kind"] for i in mine] == ["correction.rejected", "correction.approved"]
    assert "No gate log" in mine[0]["body"]
    assert "2026-11-04" in mine[1]["body"] and "Checked" in mine[1]["body"]


def test_finalizing_payroll_notifies_employees_and_hr(client, make_user, make_employee, db_session):
    hr = auth(make_user(email="hr@t.com", role=Role.hr))
    admin = auth(make_user(email="admin@t.com", role=Role.admin))
    ann_user, _ = make_employee(email="a@t.com", name="Ann")
    bob_user, _ = make_employee(email="b@t.com", name="Bob")
    bob_user.is_active = False  # left the company: cannot sign in, so there is no point notifying
    db_session.commit()

    run = client.post("/payroll/runs", json={"year": 2026, "month": 2}, headers=hr).json()
    assert notes(client, auth(ann_user))["total"] == 0  # a draft tells nobody
    assert notes(client, hr)["total"] == 0

    assert client.post(f"/payroll/runs/{run['id']}/finalize", headers=admin).status_code == 200

    payslips = client.get(f"/payroll/runs/{run['id']}/payslips", headers=hr).json()["items"]
    ann_slip = next(p for p in payslips if p["employee"]["full_name"] == "Ann")

    ann = notes(client, auth(ann_user))
    assert ann["total"] == 1
    assert ann["items"][0]["kind"] == "payslip.ready"
    assert "February 2026" in ann["items"][0]["body"]
    assert ann["items"][0]["entity_type"] == "payslip" and ann["items"][0]["entity_id"] == ann_slip["id"]
    assert db_session.query(Notification).filter(Notification.user_id == bob_user.id).count() == 0

    hr_note = notes(client, hr)["items"][0]
    assert hr_note["kind"] == "payroll.finalized"
    assert "admin@t.com" in hr_note["body"] and "February 2026" in hr_note["body"]
    assert notes(client, admin)["total"] == 0


# ---------- Reading and marking ----------

def test_read_state_scope_and_paging(client, make_user, db_session):
    ann = make_user(email="a@t.com", role=Role.employee)
    bob = make_user(email="b@t.com", role=Role.employee)
    for i in range(3):
        db_session.add(Notification(user_id=ann.id, kind="test", title=f"Note {i}", body="Hello"))
    db_session.add(Notification(user_id=bob.id, kind="test", title="Bob's note", body="Private"))
    db_session.commit()
    bob_note_id = db_session.query(Notification).filter(Notification.user_id == bob.id).one().id
    a, b = auth(ann), auth(bob)

    data = notes(client, a)
    assert data["total"] == 3 and data["unread"] == 3
    assert [i["title"] for i in data["items"]] == ["Note 2", "Note 1", "Note 0"]  # newest first
    assert client.get("/notifications/unread-count", headers=a).json() == {"unread": 3}

    first_id = data["items"][0]["id"]
    read = client.post(f"/notifications/{first_id}/read", headers=a)
    assert read.status_code == 200
    assert read.json()["is_read"] is True and read.json()["read_at"] is not None
    again = client.post(f"/notifications/{first_id}/read", headers=a)
    assert again.json()["read_at"] == read.json()["read_at"]  # reading twice changes nothing

    assert client.get("/notifications/unread-count", headers=a).json() == {"unread": 2}
    assert notes(client, a, "?unread_only=true")["total"] == 2
    assert notes(client, a)["total"] == 3
    assert notes(client, a, "?unread_only=true")["unread"] == 2

    page = notes(client, a, "?limit=2&offset=2")
    assert len(page["items"]) == 1 and page["total"] == 3

    # Nobody can touch someone else's notification
    assert client.post(f"/notifications/{bob_note_id}/read", headers=a).status_code == 404
    assert client.post("/notifications/9999/read", headers=a).status_code == 404
    assert notes(client, b)["unread"] == 1

    assert client.post("/notifications/read-all", headers=a).json() == {"marked": 2}
    assert client.post("/notifications/read-all", headers=a).json() == {"marked": 0}
    assert notes(client, a)["unread"] == 0
    assert notes(client, b)["unread"] == 1  # Bob is untouched

    for bad in ("limit=0", "limit=1000", "offset=-1"):
        assert client.get(f"/notifications?{bad}", headers=a).status_code == 422


def test_everyone_can_use_it_but_only_when_logged_in(client, make_user):
    for role in (Role.admin, Role.hr, Role.employee):
        headers = auth(make_user(email=f"{role.value}@t.com", role=role))
        assert client.get("/notifications", headers=headers).status_code == 200
        assert client.get("/notifications/unread-count", headers=headers).status_code == 200
        assert client.post("/notifications/read-all", headers=headers).status_code == 200
    assert client.get("/notifications").status_code == 401
    assert client.get("/notifications/unread-count").status_code == 401
    assert client.post("/notifications/read-all").status_code == 401
    assert client.post("/notifications/1/read").status_code == 401