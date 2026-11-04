from datetime import date, timedelta

import pytest

from app.core.security import create_access_token
from app.models.org import Department, Designation
from app.models.user import Role, User

JOINED = (date.today() - timedelta(days=100)).isoformat()


def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value)}"}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def hr(make_user):
    return auth(make_user(email="hr@t.com", role=Role.hr))


@pytest.fixture()
def admin(make_user):
    return auth(make_user(email="admin@t.com", role=Role.admin))


@pytest.fixture()
def org(db_session):
    dept = Department(name="Engineering")
    desig = Designation(name="Developer")
    db_session.add_all([dept, desig])
    db_session.commit()
    return {"dept": dept.id, "desig": desig.id}


def payload(org, **over):
    body = {
        "email": "emp1@test.com",
        "full_name": "Ali Khan",
        "phone": "0300-1234567",
        "address": "Karachi",
        "department_id": org["dept"],
        "designation_id": org["desig"],
        "join_date": JOINED,
        "basic_salary": 50000,
    }
    body.update(over)
    return body


def register(client, hr, org, **over):
    return client.post("/employees", json=payload(org, **over), headers=hr)


def full_update(emp, **over):
    body = {
        "full_name": emp["full_name"],
        "phone": emp["phone"],
        "address": emp["address"],
        "gender": emp["gender"],
        "department_id": emp["department"]["id"],
        "designation_id": emp["designation"]["id"],
        "join_date": emp["join_date"],
        "basic_salary": emp["basic_salary"],
        "status": emp["status"],
    }
    body.update(over)
    return body


def login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


def employee_token(client, email, temp, new="MyOwnPass123"):
    token = login(client, email, temp).json()["access_token"]
    r = client.post(
        "/auth/change-password",
        json={"current_password": temp, "new_password": new},
        headers=bearer(token),
    )
    assert r.status_code == 200
    return token


# ---------- Registration ----------

def test_register_creates_employee_and_login(client, hr, org):
    r = register(client, hr, org, email="Emp1@Test.com")
    assert r.status_code == 201
    body = r.json()
    emp = body["employee"]
    assert emp["employee_code"] == "EMP-0001"
    assert emp["email"] == "emp1@test.com"
    assert emp["full_name"] == "Ali Khan"
    assert emp["status"] == "active"
    assert emp["basic_salary"] == 50000
    assert emp["department"]["name"] == "Engineering"
    assert emp["designation"]["name"] == "Developer"
    assert "password_hash" not in r.text

    first = login(client, "emp1@test.com", body["temporary_password"])
    assert first.status_code == 200
    assert first.json()["role"] == "employee"
    assert first.json()["must_change_password"] is True

    second = register(client, hr, org, email="emp2@test.com")
    assert second.json()["employee"]["employee_code"] == "EMP-0002"


def test_new_employee_must_change_password_first(client, hr, org):
    temp = register(client, hr, org).json()["temporary_password"]
    token = login(client, "emp1@test.com", temp).json()["access_token"]
    assert client.get("/employees/me", headers=bearer(token)).status_code == 403

    token = employee_token(client, "emp1@test.com", temp)
    assert client.get("/employees/me", headers=bearer(token)).status_code == 200


def test_duplicate_email_is_409_and_creates_nothing(client, hr, org, db_session):
    assert register(client, hr, org).status_code == 201
    assert register(client, hr, org, email="EMP1@test.com").status_code == 409
    assert register(client, hr, org, email="hr@t.com").status_code == 409  # an existing login
    assert client.get("/employees", headers=hr).json()["total"] == 1
    assert db_session.query(User).count() == 2  # hr + the one employee


def test_custom_employee_code(client, hr, org, db_session):
    r = register(client, hr, org, employee_code="dev-7")
    assert r.json()["employee"]["employee_code"] == "DEV-7"

    clash = register(client, hr, org, email="other@test.com", employee_code="DEV-7")
    assert clash.status_code == 409
    assert db_session.query(User).filter(User.email == "other@test.com").count() == 0


def test_invalid_input_is_rejected(client, hr, org):
    bad_inputs = [
        {"basic_salary": 0},
        {"basic_salary": -5},
        {"basic_salary": 10**12},
        {"phone": "abc"},
        {"full_name": "   "},
        {"join_date": "2099-01-01"},
        {"join_date": "1980-01-01"},
        {"status": "exited"},
        {"email": "not-an-email"},
        {"employee_code": "bad code!"},
    ]
    for over in bad_inputs:
        assert register(client, hr, org, **over).status_code == 422, over

    missing = payload(org)
    del missing["department_id"]
    assert client.post("/employees", json=missing, headers=hr).status_code == 422


def test_unknown_or_inactive_department_and_designation(client, hr, org, db_session):
    inactive = Department(name="Closed", is_active=False)
    db_session.add(inactive)
    db_session.commit()

    assert register(client, hr, org, department_id=999).status_code == 400
    assert register(client, hr, org, department_id=inactive.id).status_code == 400
    assert register(client, hr, org, designation_id=999).status_code == 400
    # No login was created by any failed attempt
    assert db_session.query(User).filter(User.email == "emp1@test.com").count() == 0


def test_only_hr_can_register(client, admin, org, make_user):
    emp = auth(make_user(email="e@t.com", role=Role.employee))
    assert register(client, admin, org).status_code == 403
    assert register(client, emp, org).status_code == 403
    assert client.post("/employees", json=payload(org)).status_code == 401


# ---------- Viewing: directory and detail ----------

def test_admin_can_view_but_not_change(client, hr, admin, org):
    emp = register(client, hr, org).json()["employee"]
    assert client.get("/employees", headers=admin).status_code == 200
    detail = client.get(f"/employees/{emp['id']}", headers=admin)
    assert detail.status_code == 200
    assert detail.json()["basic_salary"] == 50000

    assert client.put(f"/employees/{emp['id']}", json=full_update(emp), headers=admin).status_code == 403
    exit_body = {"exit_date": date.today().isoformat()}
    assert client.post(f"/employees/{emp['id']}/exit", json=exit_body, headers=admin).status_code == 403


def test_list_search_filters_and_paging(client, hr, org, db_session):
    sales = Department(name="Sales")
    db_session.add(sales)
    db_session.commit()

    register(client, hr, org, email="ali@t.com", full_name="Ali Khan")
    register(client, hr, org, email="bob@t.com", full_name="Bob Stone", department_id=sales.id)
    third = register(client, hr, org, email="cara@t.com", full_name="Cara Lee").json()["employee"]
    exit_body = {"exit_date": date.today().isoformat()}
    assert client.post(f"/employees/{third['id']}/exit", json=exit_body, headers=hr).status_code == 200

    everyone = client.get("/employees", headers=hr)
    assert everyone.json()["total"] == 3
    assert everyone.json()["items"][0]["full_name"] == "Ali Khan"  # sorted by name
    assert "basic_salary" not in everyone.text  # no salary in the directory list

    def total(query):
        return client.get(f"/employees?{query}", headers=hr).json()["total"]

    assert total("search=ali") == 1
    assert total("search=BOB@T") == 1
    assert total("search=EMP-0002") == 1
    assert total(f"department_id={sales.id}") == 1
    assert total(f"designation_id={org['desig']}") == 3
    assert total("status=exited") == 1
    assert total("status=active") == 2

    page1 = client.get("/employees?limit=2&offset=0", headers=hr).json()
    page2 = client.get("/employees?limit=2&offset=2", headers=hr).json()
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1
    assert page1["total"] == 3

    assert client.get("/employees?limit=0", headers=hr).status_code == 422
    assert client.get("/employees?limit=1000", headers=hr).status_code == 422
    assert client.get("/employees?status=bogus", headers=hr).status_code == 422


def test_unknown_employee_is_404(client, hr):
    assert client.get("/employees/999", headers=hr).status_code == 404
    assert client.post("/employees/999/exit", json={"exit_date": date.today().isoformat()}, headers=hr).status_code == 404


# ---------- Editing ----------

def test_hr_can_update_but_not_email_or_code(client, hr, org):
    emp = register(client, hr, org).json()["employee"]
    body = full_update(
        emp,
        full_name="Ali Raza",
        phone="0333-1112222",
        basic_salary=60000,
        status="probation",
        email="hacker@test.com",  # not part of the schema, so it is ignored
        employee_code="HACK-1",
    )
    r = client.put(f"/employees/{emp['id']}", json=body, headers=hr)
    assert r.status_code == 200
    again = client.get(f"/employees/{emp['id']}", headers=hr).json()
    assert again["full_name"] == "Ali Raza"
    assert again["phone"] == "0333-1112222"
    assert again["basic_salary"] == 60000
    assert again["status"] == "probation"
    assert again["email"] == "emp1@test.com"
    assert again["employee_code"] == "EMP-0001"


def test_update_cannot_set_status_to_exited(client, hr, org):
    emp = register(client, hr, org).json()["employee"]
    r = client.put(f"/employees/{emp['id']}", json=full_update(emp, status="exited"), headers=hr)
    assert r.status_code == 422


def test_update_department_rules(client, hr, org, db_session):
    emp = register(client, hr, org).json()["employee"]
    closed = Department(name="Closed", is_active=False)
    db_session.add(closed)
    db_session.commit()

    assert client.put(f"/employees/{emp['id']}", json=full_update(emp, department_id=closed.id), headers=hr).status_code == 400
    assert client.put(f"/employees/{emp['id']}", json=full_update(emp, designation_id=999), headers=hr).status_code == 400

    # Their own department gets deactivated later: editing other fields must still work
    own = db_session.get(Department, org["dept"])
    own.is_active = False
    db_session.commit()
    r = client.put(f"/employees/{emp['id']}", json=full_update(emp, phone="0300-9999999"), headers=hr)
    assert r.status_code == 200


# ---------- Exit ----------

def test_exit_deactivates_the_login(client, hr, org):
    created = register(client, hr, org).json()
    emp = created["employee"]
    token = employee_token(client, "emp1@test.com", created["temporary_password"])
    assert client.get("/employees/me", headers=bearer(token)).status_code == 200

    r = client.post(f"/employees/{emp['id']}/exit", json={"exit_date": date.today().isoformat()}, headers=hr)
    assert r.status_code == 200
    assert r.json()["status"] == "exited"
    assert r.json()["exit_date"] == date.today().isoformat()
    assert r.json()["is_active"] is False

    assert login(client, "emp1@test.com", "MyOwnPass123").status_code == 403
    assert client.get("/employees/me", headers=bearer(token)).status_code == 401  # old token dies too


def test_exit_rules(client, hr, org):
    emp = register(client, hr, org).json()["employee"]
    path = f"/employees/{emp['id']}/exit"

    before_join = (date.fromisoformat(JOINED) - timedelta(days=1)).isoformat()
    in_future = (date.today() + timedelta(days=5)).isoformat()
    assert client.post(path, json={"exit_date": before_join}, headers=hr).status_code == 400
    assert client.post(path, json={"exit_date": in_future}, headers=hr).status_code == 400
    assert client.post(path, json={"exit_date": "garbage"}, headers=hr).status_code == 422

    today = {"exit_date": date.today().isoformat()}
    assert client.post(path, json=today, headers=hr).status_code == 200
    assert client.post(path, json=today, headers=hr).status_code == 409
    assert client.put(f"/employees/{emp['id']}", json=full_update(emp), headers=hr).status_code == 409


# ---------- Employee self-service and data scope ----------

def test_employee_sees_own_profile_without_salary(client, hr, org):
    created = register(client, hr, org).json()
    token = employee_token(client, "emp1@test.com", created["temporary_password"])

    r = client.get("/employees/me", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "emp1@test.com"
    assert body["department"]["name"] == "Engineering"
    assert "basic_salary" not in r.text


def test_employee_can_only_edit_phone_and_address(client, hr, org):
    created = register(client, hr, org).json()
    emp_id = created["employee"]["id"]
    token = employee_token(client, "emp1@test.com", created["temporary_password"])

    r = client.put(
        "/employees/me",
        json={
            "phone": "0311-7654321",
            "address": "New Street 5",
            "basic_salary": 999999,
            "full_name": "Hacker",
            "department_id": 99,
        },
        headers=bearer(token),
    )
    assert r.status_code == 200
    assert r.json()["phone"] == "0311-7654321"
    assert r.json()["address"] == "New Street 5"

    as_hr = client.get(f"/employees/{emp_id}", headers=hr).json()
    assert as_hr["basic_salary"] == 50000
    assert as_hr["full_name"] == "Ali Khan"
    assert as_hr["department"]["id"] == created["employee"]["department"]["id"]

    bad = client.put("/employees/me", json={"phone": "abc"}, headers=bearer(token))
    assert bad.status_code == 422


def test_employee_cannot_see_other_employees(client, hr, org):
    first = register(client, hr, org, email="e1@test.com").json()
    second = register(client, hr, org, email="e2@test.com").json()
    token = employee_token(client, "e1@test.com", first["temporary_password"])
    other_id = second["employee"]["id"]
    other = full_update(second["employee"])

    assert client.get("/employees", headers=bearer(token)).status_code == 403
    assert client.get(f"/employees/{other_id}", headers=bearer(token)).status_code == 403
    assert client.put(f"/employees/{other_id}", json=other, headers=bearer(token)).status_code == 403
    exit_body = {"exit_date": date.today().isoformat()}
    assert client.post(f"/employees/{other_id}/exit", json=exit_body, headers=bearer(token)).status_code == 403
    assert client.post("/employees", json=payload(org, email="x@test.com"), headers=bearer(token)).status_code == 403


def test_staff_logins_have_no_employee_profile(client, hr, admin):
    assert client.get("/employees/me", headers=hr).status_code == 403
    assert client.get("/employees/me", headers=admin).status_code == 403
    assert client.get("/employees/me").status_code == 401
    