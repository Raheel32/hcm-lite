from app.core.security import create_access_token
from app.models.user import Role

SETUP = {
    "company_name": "Test Co",
    "admin_email": "admin@test.com",
    "admin_password": "Admin12345",
}

VALID_UPDATE = {
    "office_start": "09:30:00",
    "grace_minutes": 10,
    "half_day_hours": 4.5,
    "weekend_days": [6],
    "office_lat": 24.8607,
    "office_lng": 67.0011,
    "radius_m": 200,
}


def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value)}"}


def admin_headers(client):
    client.post("/setup", json=SETUP)
    token = client.post(
        "/auth/login", json={"email": "admin@test.com", "password": "Admin12345"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_settings_need_setup_first(client, make_user):
    admin = make_user(email="a@t.com", role=Role.admin)
    assert client.get("/settings", headers=auth(admin)).status_code == 409


def test_defaults_match_the_brief(client):
    r = client.get("/settings", headers=admin_headers(client))
    body = r.json()
    assert r.status_code == 200
    assert body["office_start"] == "09:00:00"
    assert body["grace_minutes"] == 15
    assert body["half_day_hours"] == 5.0
    assert body["weekend_days"] == [5, 6]
    assert body["radius_m"] == 150
    assert body["office_lat"] is None


def test_admin_can_update_and_it_persists(client):
    headers = admin_headers(client)
    r = client.put("/settings", json=VALID_UPDATE, headers=headers)
    assert r.status_code == 200
    again = client.get("/settings", headers=headers).json()
    assert again["office_start"] == "09:30:00"
    assert again["grace_minutes"] == 10
    assert again["weekend_days"] == [6]
    assert again["office_lat"] == 24.8607


def test_hr_can_view_but_not_change(client, make_user):
    admin_headers(client)
    hr = make_user(email="hr@t.com", role=Role.hr)
    assert client.get("/settings", headers=auth(hr)).status_code == 200
    assert client.put("/settings", json=VALID_UPDATE, headers=auth(hr)).status_code == 403


def test_employee_has_no_access(client, make_user):
    admin_headers(client)
    emp = make_user(email="emp@t.com", role=Role.employee)
    assert client.get("/settings", headers=auth(emp)).status_code == 403
    assert client.put("/settings", json=VALID_UPDATE, headers=auth(emp)).status_code == 403


def test_no_token_is_401(client):
    assert client.get("/settings").status_code == 401
    assert client.put("/settings", json=VALID_UPDATE).status_code == 401


def test_bad_values_are_rejected(client):
    headers = admin_headers(client)
    bad_inputs = [
        {**VALID_UPDATE, "grace_minutes": 500},
        {**VALID_UPDATE, "weekend_days": [9]},
        {**VALID_UPDATE, "weekend_days": [5, 5]},
        {**VALID_UPDATE, "office_lat": 200},
        {**VALID_UPDATE, "radius_m": 1},
        {**VALID_UPDATE, "office_lng": None},  # latitude without longitude
    ]
    for payload in bad_inputs:
        assert client.put("/settings", json=payload, headers=headers).status_code == 422