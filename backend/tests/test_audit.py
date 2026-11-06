import json
from datetime import date, datetime, timedelta

import pytest

from app.core import clock
from app.core.security import create_access_token
from app.models.audit import AuditLog, AuditLogIsReadOnly
from app.models.company import Company
from app.models.employee import Employee, EmployeeStatus
from app.models.leave_config import LeaveType
from app.models.org import Department, Designation
from app.models.user import Role

SETUP = {"company_name": "Test Co", "admin_email": "admin@test.com", "admin_password": "Admin12345"}
JOINED = (date.today() - timedelta(days=100)).isoformat()


def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value)}"}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


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
    annual = LeaveType(name="Annual", yearly_quota=14, is_paid=True)
    db_session.add(annual)
    db_session.commit()
    return {"Annual": annual.id}


@pytest.fixture()
def make_employee(db_session, make_user, org):
    def _make(email="emp@t.com", name="Ali Khan", join=date(2025, 1, 1)):
        user = make_user(email=email, role=Role.employee)
        emp = Employee(
            user_id=user.id, employee_code="EMP-0001", full_name=name,
            department_id=org["dept"], designation_id=org["desig"], join_date=join,
            status=EmployeeStatus.active, basic_salary=50000,
        )
        db_session.add(emp)
        db_session.commit()
        db_session.refresh(emp)
        return user, emp

    return _make


@pytest.fixture()
def admin_user(make_user):
    return make_user(email="admin@t.com", role=Role.admin)


@pytest.fixture()
def admin(admin_user):
    return auth(admin_user)


@pytest.fixture()
def hr(make_user):
    return auth(make_user(email="hr@t.com", role=Role.hr))


def entries(client, headers, query=""):
    r = client.get(f"/audit?limit=200{query}", headers=headers)
    assert r.status_code == 200
    return r.json()


def register_body(org, **over):
    body = {
        "email": "emp1@test.com", "full_name": "Ali Khan", "phone": "0300-1234567", "address": "Karachi",
        "department_id": org["dept"], "designation_id": org["desig"], "join_date": JOINED, "basic_salary": 50000,
    }
    body.update(over)
    return body


def employee_update(emp, **over):
    body = {
        "full_name": emp["full_name"], "phone": emp["phone"], "address": emp["address"], "gender": emp["gender"],
        "department_id": emp["department"]["id"], "designation_id": emp["designation"]["id"],
        "join_date": emp["join_date"], "basic_salary": emp["basic_salary"], "status": emp["status"],
    }
    body.update(over)
    return body


# ---------- Who can read it ----------

def test_only_admin_can_read_the_audit_log(client, admin, hr, make_user):
    emp = auth(make_user(email="e@t.com", role=Role.employee))
    for path in ("/audit", "/audit/actions"):
        assert client.get(path, headers=admin).status_code == 200
        assert client.get(path, headers=hr).status_code == 403
        assert client.get(path, headers=emp).status_code == 403
        assert client.get(path).status_code == 401


def test_the_audit_log_cannot_be_changed(client, admin, db_session):
    client.post("/departments", json={"name": "Engineering"}, headers=admin)
    assert db_session.query(AuditLog).count() == 1

    entry = db_session.query(AuditLog).first()
    entry.summary = "tampered"
    with pytest.raises(AuditLogIsReadOnly):
        db_session.commit()
    db_session.rollback()

    db_session.delete(db_session.query(AuditLog).first())
    with pytest.raises(AuditLogIsReadOnly):
        db_session.commit()
    db_session.rollback()

    assert db_session.query(AuditLog).count() == 1
    assert db_session.query(AuditLog).first().summary != "tampered"

    # And the API has no way to write at all
    for method in ("put", "patch", "delete", "post"):
        assert getattr(client, method)("/audit", headers=admin).status_code == 405
    assert client.delete("/audit/1", headers=admin).status_code == 404


# ---------- Phase 1: setup and passwords ----------

def test_first_run_setup_is_audited(client):
    assert client.post("/setup", json=SETUP).status_code == 201
    token = client.post("/auth/login", json={"email": "admin@test.com", "password": "Admin12345"}).json()["access_token"]
    admin = bearer(token)

    items = entries(client, admin)["items"]
    assert len(items) == 1
    entry = items[0]
    assert entry["action"] == "setup.complete"
    assert entry["entity_type"] == "company"
    assert entry["actor"]["email"] == "admin@test.com"
    assert entry["actor"]["role"] == "admin"
    assert entry["before"] is None
    assert entry["after"] == {"company": "Test Co", "admin_email": "admin@test.com"}
    assert "Admin12345" not in json.dumps(entry)

    assert client.post("/setup", json=SETUP).status_code == 409  # refused, so nothing new is logged
    assert entries(client, admin)["total"] == 1


# ---------- Phase 2: organization ----------

def test_settings_changes_are_logged_as_a_diff(client, admin, company):
    body = {
        "office_start": "09:00:00", "grace_minutes": 10, "half_day_hours": 5,
        "weekend_days": [5, 6], "office_lat": None, "office_lng": None, "radius_m": 150,
    }
    assert client.put("/settings", json=body, headers=admin).status_code == 200
    items = entries(client, admin, "&entity_type=settings")["items"]
    assert len(items) == 1
    assert items[0]["action"] == "settings.update"
    assert items[0]["before"] == {"grace_minutes": 15}
    assert items[0]["after"] == {"grace_minutes": 10}

    # The same values again change nothing, so nothing is logged
    assert client.put("/settings", json=body, headers=admin).status_code == 200
    assert entries(client, admin, "&entity_type=settings")["total"] == 1

    bigger = {**body, "office_start": "09:30:00", "weekend_days": [6], "office_lat": 24.86, "office_lng": 67.0, "radius_m": 200}
    assert client.put("/settings", json=bigger, headers=admin).status_code == 200
    latest = entries(client, admin, "&entity_type=settings")["items"][0]
    assert latest["before"] == {
        "office_start": "09:00:00", "weekend_days": [5, 6], "office_lat": None, "office_lng": None, "radius_m": 150,
    }
    assert latest["after"] == {
        "office_start": "09:30:00", "weekend_days": [6], "office_lat": 24.86, "office_lng": 67.0, "radius_m": 200,
    }


def test_departments_and_designations_are_logged(client, admin):
    dept = client.post("/departments", json={"name": "Engineering"}, headers=admin).json()
    client.put(f"/departments/{dept['id']}", json={"name": "Engineering", "is_active": False}, headers=admin)
    client.put(f"/departments/{dept['id']}", json={"name": "Engineering", "is_active": False}, headers=admin)  # no change
    client.post("/designations", json={"name": "Developer"}, headers=admin)
    assert client.post("/departments", json={"name": "engineering"}, headers=admin).status_code == 409  # failed: not logged

    items = entries(client, admin, "&entity_type=department")["items"]
    assert [i["action"] for i in items] == ["department.update", "department.create"]  # newest first
    assert items[1]["after"] == {"name": "Engineering", "is_active": True}
    assert items[1]["before"] is None
    assert items[0]["before"] == {"is_active": True}
    assert items[0]["after"] == {"is_active": False}
    assert items[0]["entity_id"] == dept["id"]
    assert entries(client, admin, "&entity_type=designation")["total"] == 1


def test_leave_types_and_holidays_are_logged(client, admin):
    lt = client.post("/leave-types", json={"name": "Annual", "yearly_quota": 14, "is_paid": True}, headers=admin).json()
    client.put(
        f"/leave-types/{lt['id']}",
        json={"name": "Annual", "yearly_quota": 20, "is_paid": True, "is_active": True},
        headers=admin,
    )
    holiday = client.post("/holidays", json={"name": "Labour Day", "date": "2026-05-01"}, headers=admin).json()
    client.put(f"/holidays/{holiday['id']}", json={"name": "Workers Day", "date": "2026-05-01"}, headers=admin)
    client.delete(f"/holidays/{holiday['id']}", headers=admin)

    types = entries(client, admin, "&entity_type=leave_type")["items"]
    assert [i["action"] for i in types] == ["leave_type.update", "leave_type.create"]
    assert types[0]["before"] == {"yearly_quota": 14}
    assert types[0]["after"] == {"yearly_quota": 20}

    days = entries(client, admin, "&entity_type=holiday")["items"]
    assert [i["action"] for i in days] == ["holiday.delete", "holiday.update", "holiday.create"]
    assert days[2]["after"] == {"date": "2026-05-01", "name": "Labour Day"}
    assert days[1]["before"] == {"name": "Labour Day"}
    assert days[1]["after"] == {"name": "Workers Day"}
    assert days[0]["before"] == {"date": "2026-05-01", "name": "Workers Day"}
    assert days[0]["after"] is None


def test_user_management_is_logged_without_any_password(client, admin):
    created = client.post("/users", json={"email": "hr2@t.com"}, headers=admin).json()
    uid, temp = created["user"]["id"], created["temporary_password"]
    client.post(f"/users/{uid}/deactivate", headers=admin)
    client.post(f"/users/{uid}/deactivate", headers=admin)  # already inactive: nothing new
    client.post(f"/users/{uid}/activate", headers=admin)
    reset = client.post(f"/users/{uid}/reset-password", headers=admin).json()

    items = entries(client, admin, f"&entity_type=user&entity_id={uid}")["items"]
    assert [i["action"] for i in items] == ["user.reset_password", "user.activate", "user.deactivate", "user.create"]
    assert items[3]["after"] == {"email": "hr2@t.com", "role": "hr"}
    assert items[2]["before"] == {"is_active": True}
    assert items[2]["after"] == {"is_active": False}
    assert all(i["actor"]["email"] == "admin@t.com" for i in items)

    text = json.dumps(items)
    assert temp not in text
    assert reset["temporary_password"] not in text
    assert "$2b$" not in text


# ---------- Phase 3 and 6: employees and salary ----------

def test_the_employee_lifecycle_is_logged(client, hr, admin, org):
    r = client.post("/employees", json=register_body(org), headers=hr)
    assert r.status_code == 201
    emp, temp = r.json()["employee"], r.json()["temporary_password"]

    token = client.post("/auth/login", json={"email": "emp1@test.com", "password": temp}).json()["access_token"]
    mine = bearer(token)
    change = client.post(
        "/auth/change-password", json={"current_password": temp, "new_password": "BrandNew123"}, headers=mine
    )
    assert change.status_code == 200

    update = employee_update(emp, full_name="Ali Raza", basic_salary=60000)
    assert client.put(f"/employees/{emp['id']}", json=update, headers=hr).status_code == 200
    contact = {"phone": "0311-7654321", "address": "New Street 5"}
    assert client.put("/employees/me", json=contact, headers=mine).status_code == 200
    salary = client.put(f"/payroll/salary-structure/{emp['id']}", json={"basic_salary": 70000}, headers=hr)
    assert salary.status_code == 200
    exit_body = {"exit_date": date.today().isoformat()}
    assert client.post(f"/employees/{emp['id']}/exit", json=exit_body, headers=hr).status_code == 200

    items = entries(client, admin, f"&entity_type=employee&entity_id={emp['id']}")["items"]
    assert [i["action"] for i in items] == [
        "employee.exit", "salary.update", "employee.self_update", "employee.update", "employee.create",
    ]
    assert [i["actor"]["email"] for i in items] == [
        "hr@t.com", "hr@t.com", "emp1@test.com", "hr@t.com", "hr@t.com",
    ]
    exit_entry, salary_entry, self_entry, update_entry, create_entry = items

    assert create_entry["before"] is None
    assert create_entry["after"]["email"] == "emp1@test.com"
    assert create_entry["after"]["employee_code"] == "EMP-0001"
    assert create_entry["after"]["basic_salary"] == 50000
    assert "EMP-0001" in create_entry["summary"]

    assert update_entry["before"] == {"full_name": "Ali Khan", "basic_salary": 50000}
    assert update_entry["after"] == {"full_name": "Ali Raza", "basic_salary": 60000}
    assert self_entry["before"] == {"phone": "0300-1234567", "address": "Karachi"}
    assert self_entry["after"] == {"phone": "0311-7654321", "address": "New Street 5"}
    assert salary_entry["before"] == {"basic_salary": 60000}
    assert salary_entry["after"] == {"basic_salary": 70000}
    assert exit_entry["before"] == {"status": "active", "exit_date": None, "login_active": True}
    assert exit_entry["after"] == {"status": "exited", "exit_date": date.today().isoformat(), "login_active": False}

    changed = entries(client, admin, "&action=auth.password_changed")["items"]
    assert len(changed) == 1
    assert changed[0]["actor"]["email"] == "emp1@test.com"
    assert changed[0]["actor"]["role"] == "employee"
    assert changed[0]["before"] == {"must_change_password": True}
    assert changed[0]["after"] == {"must_change_password": False}

    everything = entries(client, admin)
    assert everything["total"] == 6
    text = json.dumps(everything)
    assert temp not in text and "BrandNew123" not in text and "$2b$" not in text


def test_a_failed_registration_leaves_no_entry(client, hr, admin, org):
    assert client.post("/employees", json=register_body(org), headers=hr).status_code == 201
    assert client.post("/employees", json=register_body(org), headers=hr).status_code == 409  # same email
    assert client.post("/employees", json=register_body(org, email="x@t.com", department_id=999), headers=hr).status_code == 400
    assert entries(client, admin, "&action=employee.create")["total"] == 1


# ---------- Phases 4, 5: decisions ----------

def test_leave_and_correction_decisions_are_logged(client, admin, hr, make_employee, types):
    user, _ = make_employee()
    emp = auth(user)

    def leave(start, end):
        body = {"leave_type_id": types["Annual"], "start_date": start, "end_date": end, "reason": "Family event"}
        return client.post("/leave", json=body, headers=emp).json()

    first, second = leave("2026-11-09", "2026-11-10"), leave("2026-11-16", "2026-11-17")
    assert client.post(f"/leave/{first['id']}/approve", json={"comment": "Enjoy"}, headers=hr).status_code == 200
    assert client.post(f"/leave/{second['id']}/reject", json={"comment": "Team is short"}, headers=hr).status_code == 200

    items = entries(client, admin, "&entity_type=leave_request")["items"]
    assert [i["action"] for i in items] == ["leave.reject", "leave.approve"]  # applying is not logged, deciding is
    approved = items[1]
    assert approved["entity_id"] == first["id"]
    assert approved["actor"]["email"] == "hr@t.com"
    assert approved["before"] == {"status": "pending"}
    assert approved["after"] == {"status": "approved", "comment": "Enjoy"}
    assert "Ali Khan" in approved["summary"]
    assert items[0]["after"] == {"status": "rejected", "comment": "Team is short"}

    def correction(day):
        body = {"date": day, "check_in": "09:00", "check_out": "17:00", "reason": "Forgot to check in"}
        return client.post("/attendance/corrections", json=body, headers=emp).json()

    c1, c2 = correction("2026-11-04"), correction("2026-11-03")
    assert client.post(f"/attendance/corrections/{c1['id']}/approve", headers=hr).status_code == 200
    rejected = client.post(f"/attendance/corrections/{c2['id']}/reject", json={"comment": "No gate log"}, headers=hr)
    assert rejected.status_code == 200

    items = entries(client, admin, "&entity_type=attendance_correction")["items"]
    assert [i["action"] for i in items] == ["correction.reject", "correction.approve"]
    assert items[1]["before"] is None  # the day had no record before
    assert items[1]["after"] == {
        "check_in": "2026-11-04T09:00:00+05:00", "check_out": "2026-11-04T17:00:00+05:00",
        "status": "present", "is_late": False,
    }
    assert items[0]["after"] == {"status": "rejected", "comment": "No gate log"}


def test_refused_decisions_leave_no_entry(client, admin, hr, make_employee, types):
    user, _ = make_employee()
    body = {"leave_type_id": types["Annual"], "start_date": "2026-11-09", "end_date": "2026-11-10", "reason": "Family event"}
    leave = client.post("/leave", json=body, headers=auth(user)).json()
    assert client.post(f"/leave/{leave['id']}/approve", headers=admin).status_code == 403  # Admin cannot decide
    assert client.post(f"/leave/{leave['id']}/reject", json={"comment": "No"}, headers=hr).status_code == 422
    assert entries(client, admin, "&entity_type=leave_request")["total"] == 0


# ---------- Phase 6: payroll ----------

def test_payroll_actions_are_logged(client, admin, hr, make_employee):
    make_employee()
    run = client.post("/payroll/runs", json={"year": 2026, "month": 2}, headers=hr).json()
    assert client.post(f"/payroll/runs/{run['id']}/recalculate", headers=hr).status_code == 200
    assert client.post(f"/payroll/runs/{run['id']}/finalize", headers=admin).status_code == 200
    other = client.post("/payroll/runs", json={"year": 2026, "month": 1}, headers=hr).json()
    assert client.delete(f"/payroll/runs/{other['id']}", headers=hr).status_code == 204

    items = entries(client, admin, "&entity_type=payroll_run")["items"]
    assert [i["action"] for i in items] == [
        "payroll.delete_run", "payroll.create_run", "payroll.finalize", "payroll.recalculate", "payroll.create_run",
    ]
    assert [i["actor"]["role"] for i in items] == ["hr", "hr", "admin", "hr", "hr"]
    finalize = items[2]
    assert finalize["entity_id"] == run["id"]
    assert finalize["before"] == {"status": "draft"}
    assert finalize["after"]["status"] == "finalized"
    assert finalize["after"]["employees"] == 1
    assert "2026-02" in finalize["summary"]
    assert items[4]["after"]["period"] == "2026-02"
    assert items[0]["before"]["period"] == "2026-01"

    # A locked run refuses to change, and refusals are not logged
    assert client.post(f"/payroll/runs/{run['id']}/recalculate", headers=hr).status_code == 409
    assert entries(client, admin, "&entity_type=payroll_run")["total"] == 5


# ---------- Filters, paging, actions ----------

def test_filters_paging_and_actions(client, admin, admin_user):
    for name in ("Engineering", "Sales", "Finance"):
        client.post("/departments", json={"name": name}, headers=admin)
    client.post("/designations", json={"name": "Developer"}, headers=admin)

    def total(query):
        return entries(client, admin, query)["total"]

    assert total("") == 4
    assert total("&entity_type=department") == 3
    assert total("&action=designation.create") == 1
    assert total(f"&actor_id={admin_user.id}") == 4
    assert total("&actor_id=999") == 0
    assert total("&entity_id=1") == 2                          # department 1 and designation 1
    assert total("&entity_id=1&entity_type=department") == 1
    assert total("&search=Sales") == 1
    assert total("&search=ADMIN@T.COM") == 4                   # matches the actor, ignoring capitals

    today = datetime.now(clock.office_tz()).date()
    tomorrow, yesterday = today + timedelta(days=1), today - timedelta(days=1)
    assert total(f"&date_from={today}&date_to={today}") == 4
    assert total(f"&date_from={tomorrow}") == 0
    assert total(f"&date_to={yesterday}") == 0

    first = entries(client, admin, "&limit=2&offset=0")
    last = entries(client, admin, "&limit=2&offset=2")
    assert len(first["items"]) == 2 and len(last["items"]) == 2 and first["total"] == 4
    assert first["items"][0]["id"] > last["items"][-1]["id"]  # newest first

    assert client.get("/audit/actions", headers=admin).json() == ["department.create", "designation.create"]

    for bad in ("limit=0", "limit=1000", "offset=-1", "date_from=garbage", "entity_id=abc"):
        assert client.get(f"/audit?{bad}", headers=admin).status_code == 422
    assert client.get(f"/audit?date_from={tomorrow}&date_to={yesterday}", headers=admin).status_code == 400