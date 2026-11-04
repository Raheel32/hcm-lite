from app.models.user import Role

SETUP = {
    "company_name": "Test Co",
    "admin_email": "admin@test.com",
    "admin_password": "Admin12345",
}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_first_run_setup_works_only_once(client):
    assert client.get("/setup/status").json() == {"initialized": False}
    assert client.post("/setup", json=SETUP).status_code == 201
    assert client.get("/setup/status").json() == {"initialized": True}
    assert client.post("/setup", json=SETUP).status_code == 409


def test_setup_rejects_short_password(client):
    bad = {**SETUP, "admin_password": "short"}
    assert client.post("/setup", json=bad).status_code == 422


def test_setup_admin_can_log_in(client):
    client.post("/setup", json=SETUP)
    r = login(client, "admin@test.com", "Admin12345")
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_login_success(client, make_user):
    make_user(email="hr@test.com", password="Password123", role=Role.hr)
    r = login(client, "hr@test.com", "Password123")
    body = r.json()
    assert r.status_code == 200
    assert body["role"] == "hr"
    assert body["access_token"]
    assert body["must_change_password"] is False


def test_wrong_password_and_unknown_email_look_the_same(client, make_user):
    make_user(email="hr@test.com", password="Password123")
    wrong = login(client, "hr@test.com", "WrongPass999")
    unknown = login(client, "nobody@test.com", "Password123")
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_deactivated_account_cannot_log_in(client, make_user):
    make_user(email="old@test.com", password="Password123", is_active=False)
    assert login(client, "old@test.com", "Password123").status_code == 403


def test_me_requires_a_token(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers=bearer("garbage")).status_code == 401


def test_me_returns_current_user(client, make_user):
    make_user(email="emp@test.com", password="Password123", role=Role.employee)
    token = login(client, "emp@test.com", "Password123").json()["access_token"]
    r = client.get("/auth/me", headers=bearer(token))
    assert r.status_code == 200
    assert r.json()["email"] == "emp@test.com"
    body = r.json()
    assert "password" not in body
    assert "password_hash" not in body
    assert "$2b$" not in r.text  # a bcrypt hash always starts with $2b$


def test_temporary_password_flow(client, make_user):
    make_user(email="new@test.com", password="Temp12345", must_change_password=True)

    first = login(client, "new@test.com", "Temp12345").json()
    assert first["must_change_password"] is True

    r = client.post(
        "/auth/change-password",
        json={"current_password": "Temp12345", "new_password": "BrandNew123"},
        headers=bearer(first["access_token"]),
    )
    assert r.status_code == 200

    assert login(client, "new@test.com", "Temp12345").status_code == 401
    again = login(client, "new@test.com", "BrandNew123").json()
    assert again["must_change_password"] is False


def test_change_password_rules(client, make_user):
    make_user(email="emp@test.com", password="Password123")
    token = login(client, "emp@test.com", "Password123").json()["access_token"]
    h = bearer(token)

    wrong_current = client.post(
        "/auth/change-password",
        json={"current_password": "Nope12345", "new_password": "NewPass123"},
        headers=h,
    )
    assert wrong_current.status_code == 400

    same = client.post(
        "/auth/change-password",
        json={"current_password": "Password123", "new_password": "Password123"},
        headers=h,
    )
    assert same.status_code == 400

    too_short = client.post(
        "/auth/change-password",
        json={"current_password": "Password123", "new_password": "short"},
        headers=h,
    )
    assert too_short.status_code == 422