import pytest

from app.core.security import create_access_token, generate_temp_password
from app.models.user import Role


def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value)}"}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_user(make_user):
    return make_user(email="admin@t.com", role=Role.admin)


@pytest.fixture()
def admin(admin_user):
    return auth(admin_user)


def login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_temp_password_generator():
    seen = set()
    for _ in range(50):
        pw = generate_temp_password()
        assert len(pw) == 12
        assert any(c.isupper() for c in pw)
        assert any(c.islower() for c in pw)
        assert any(c.isdigit() for c in pw)
        seen.add(pw)
    assert len(seen) == 50  # all different


def test_admin_creates_hr_login(client, admin):
    r = client.post("/users", json={"email": "HR@Test.com"}, headers=admin)
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "hr@test.com"  # stored in lowercase
    assert body["user"]["role"] == "hr"
    assert body["user"]["is_active"] is True
    assert body["user"]["must_change_password"] is True
    assert len(body["temporary_password"]) >= 8
    assert "password_hash" not in r.text
    assert "$2b$" not in r.text


def test_new_hr_can_log_in_with_temp_password_and_must_change_it(client, admin):
    temp = client.post("/users", json={"email": "hr@test.com"}, headers=admin).json()["temporary_password"]

    first = login(client, "hr@test.com", temp)
    assert first.status_code == 200
    assert first.json()["role"] == "hr"
    assert first.json()["must_change_password"] is True
    token = first.json()["access_token"]

    # Blocked from normal routes until the password is changed
    assert client.get("/settings", headers=bearer(token)).status_code == 403

    # Allowed to change the password
    r = client.post(
        "/auth/change-password",
        json={"current_password": temp, "new_password": "MyOwnPass123"},
        headers=bearer(token),
    )
    assert r.status_code == 200

    # Now normal routes work (HR can view settings, but there is no company yet -> 409, not 403)
    assert client.get("/settings", headers=bearer(token)).status_code in (200, 409)
    assert login(client, "hr@test.com", temp).status_code == 401
    assert login(client, "hr@test.com", "MyOwnPass123").json()["must_change_password"] is False


def test_duplicate_email_is_409_ignoring_case(client, admin):
    client.post("/users", json={"email": "hr@test.com"}, headers=admin)
    assert client.post("/users", json={"email": "HR@TEST.COM"}, headers=admin).status_code == 409
    assert client.post("/users", json={"email": "admin@t.com"}, headers=admin).status_code == 409


def test_bad_email_is_rejected(client, admin):
    assert client.post("/users", json={"email": "not-an-email"}, headers=admin).status_code == 422
    assert client.post("/users", json={}, headers=admin).status_code == 422


def test_role_cannot_be_chosen_by_the_caller(client, admin):
    r = client.post("/users", json={"email": "sneaky@test.com", "role": "admin"}, headers=admin)
    assert r.status_code == 201
    assert r.json()["user"]["role"] == "hr"  # extra field ignored, always HR


def test_only_admin_can_use_user_routes(client, make_user):
    hr = auth(make_user(email="hr@t.com", role=Role.hr))
    emp = auth(make_user(email="emp@t.com", role=Role.employee))
    target = make_user(email="target@t.com", role=Role.employee)

    for headers in (hr, emp):
        assert client.get("/users", headers=headers).status_code == 403
        assert client.post("/users", json={"email": "x@test.com"}, headers=headers).status_code == 403
        assert client.post(f"/users/{target.id}/deactivate", headers=headers).status_code == 403
        assert client.post(f"/users/{target.id}/activate", headers=headers).status_code == 403
        assert client.post(f"/users/{target.id}/reset-password", headers=headers).status_code == 403

    assert client.get("/users").status_code == 401
    assert client.post("/users", json={"email": "x@test.com"}).status_code == 401


def test_list_filters_search_and_paging(client, admin, make_user):
    make_user(email="hr1@t.com", role=Role.hr)
    make_user(email="hr2@t.com", role=Role.hr)
    make_user(email="emp1@t.com", role=Role.employee)
    make_user(email="gone@t.com", role=Role.employee, is_active=False)

    everyone = client.get("/users", headers=admin).json()
    assert everyone["total"] == 5  # includes the admin
    assert "password" not in str(everyone).replace("must_change_password", "")

    assert client.get("/users?role=hr", headers=admin).json()["total"] == 2
    assert client.get("/users?role=employee", headers=admin).json()["total"] == 2
    assert client.get("/users?is_active=false", headers=admin).json()["total"] == 1
    assert client.get("/users?search=emp1", headers=admin).json()["total"] == 1
    assert client.get("/users?search=EMP1", headers=admin).json()["total"] == 1

    page = client.get("/users?limit=2&offset=0", headers=admin).json()
    assert len(page["items"]) == 2
    assert page["total"] == 5
    last = client.get("/users?limit=2&offset=4", headers=admin).json()
    assert len(last["items"]) == 1

    assert client.get("/users?limit=0", headers=admin).status_code == 422
    assert client.get("/users?limit=1000", headers=admin).status_code == 422
    assert client.get("/users?offset=-1", headers=admin).status_code == 422


def test_deactivate_blocks_login_and_kills_existing_token(client, admin, make_user):
    hr = make_user(email="hr@t.com", password="Password123", role=Role.hr)
    token = login(client, "hr@t.com", "Password123").json()["access_token"]
    assert client.get("/auth/me", headers=bearer(token)).status_code == 200

    r = client.post(f"/users/{hr.id}/deactivate", headers=admin)
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    assert login(client, "hr@t.com", "Password123").status_code == 403
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401  # token no longer works


def test_activate_restores_access(client, admin, make_user):
    emp = make_user(email="emp@t.com", password="Password123", is_active=False)
    assert login(client, "emp@t.com", "Password123").status_code == 403

    r = client.post(f"/users/{emp.id}/activate", headers=admin)
    assert r.status_code == 200
    assert r.json()["is_active"] is True
    assert login(client, "emp@t.com", "Password123").status_code == 200


def test_admin_accounts_are_protected(client, admin, admin_user, make_user):
    other_admin = make_user(email="admin2@t.com", role=Role.admin)
    for target in (admin_user, other_admin):
        assert client.post(f"/users/{target.id}/deactivate", headers=admin).status_code == 400
        assert client.post(f"/users/{target.id}/reset-password", headers=admin).status_code == 400
    # Still able to use the account afterwards
    assert client.get("/users", headers=admin).status_code == 200


def test_unknown_user_is_404(client, admin):
    assert client.post("/users/999/deactivate", headers=admin).status_code == 404
    assert client.post("/users/999/activate", headers=admin).status_code == 404
    assert client.post("/users/999/reset-password", headers=admin).status_code == 404


def test_reset_password(client, admin, make_user):
    emp = make_user(email="emp@t.com", password="OldPassword123", role=Role.employee)

    r = client.post(f"/users/{emp.id}/reset-password", headers=admin)
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert r.json()["email"] == "emp@t.com"

    assert login(client, "emp@t.com", "OldPassword123").status_code == 401
    again = login(client, "emp@t.com", temp)
    assert again.status_code == 200
    assert again.json()["must_change_password"] is True

    second = client.post(f"/users/{emp.id}/reset-password", headers=admin).json()["temporary_password"]
    assert second != temp