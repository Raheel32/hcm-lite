import pytest

from app.core.security import create_access_token
from app.models.user import Role


def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value)}"}


@pytest.fixture()
def admin(make_user):
    return auth(make_user(email="admin@t.com", role=Role.admin))


@pytest.fixture()
def hr(make_user):
    return auth(make_user(email="hr@t.com", role=Role.hr))


@pytest.fixture()
def emp(make_user):
    return auth(make_user(email="emp@t.com", role=Role.employee))


# ---------- Leave types ----------

def test_leave_types_start_empty(client, admin):
    assert client.get("/leave-types", headers=admin).json() == []


def test_create_the_four_types_from_the_brief(client, admin):
    types = [
        {"name": "Annual", "yearly_quota": 14, "is_paid": True},
        {"name": "Sick", "yearly_quota": 8, "is_paid": True},
        {"name": "Casual", "yearly_quota": 10, "is_paid": True},
        {"name": "Unpaid", "yearly_quota": None, "is_paid": False},
    ]
    for t in types:
        assert client.post("/leave-types", json=t, headers=admin).status_code == 201

    result = {t["name"]: t for t in client.get("/leave-types", headers=admin).json()}
    assert result["Annual"]["yearly_quota"] == 14
    assert result["Sick"]["yearly_quota"] == 8
    assert result["Casual"]["yearly_quota"] == 10
    assert result["Unpaid"]["is_unlimited"] is True
    assert result["Unpaid"]["is_paid"] is False
    assert result["Annual"]["is_unlimited"] is False


def test_leave_type_duplicate_name_ignores_case(client, admin):
    client.post("/leave-types", json={"name": "Sick", "yearly_quota": 8}, headers=admin)
    r = client.post("/leave-types", json={"name": "SICK", "yearly_quota": 5}, headers=admin)
    assert r.status_code == 409


def test_leave_type_bad_values_rejected(client, admin):
    assert client.post("/leave-types", json={"name": "X"}, headers=admin).status_code == 422
    assert client.post("/leave-types", json={"name": "Bad", "yearly_quota": -1}, headers=admin).status_code == 422
    assert client.post("/leave-types", json={"name": "Bad", "yearly_quota": 400}, headers=admin).status_code == 422
    assert client.post("/leave-types", json={"yearly_quota": 5}, headers=admin).status_code == 422


def test_leave_type_permissions(client, admin, hr, emp):
    created = client.post("/leave-types", json={"name": "Annual", "yearly_quota": 14}, headers=admin).json()
    update = {"name": "Annual", "yearly_quota": 20, "is_paid": True, "is_active": True}

    assert client.get("/leave-types", headers=hr).status_code == 200
    assert client.post("/leave-types", json={"name": "Study", "yearly_quota": 3}, headers=hr).status_code == 403
    assert client.put(f"/leave-types/{created['id']}", json=update, headers=hr).status_code == 403

    assert client.get("/leave-types", headers=emp).status_code == 403
    assert client.post("/leave-types", json={"name": "Study", "yearly_quota": 3}, headers=emp).status_code == 403

    assert client.get("/leave-types").status_code == 401


def test_change_quota_and_deactivate(client, admin):
    created = client.post("/leave-types", json={"name": "Annual", "yearly_quota": 14}, headers=admin).json()

    r = client.put(
        f"/leave-types/{created['id']}",
        json={"name": "Annual", "yearly_quota": 20, "is_paid": True, "is_active": True},
        headers=admin,
    )
    assert r.status_code == 200
    assert r.json()["yearly_quota"] == 20

    client.put(
        f"/leave-types/{created['id']}",
        json={"name": "Annual", "yearly_quota": 20, "is_paid": True, "is_active": False},
        headers=admin,
    )
    assert client.get("/leave-types", headers=admin).json() == []
    assert len(client.get("/leave-types?include_inactive=true", headers=admin).json()) == 1


def test_leave_type_rename_to_existing_is_409_and_unknown_is_404(client, admin):
    client.post("/leave-types", json={"name": "Sick", "yearly_quota": 8}, headers=admin)
    other = client.post("/leave-types", json={"name": "Casual", "yearly_quota": 10}, headers=admin).json()

    clash = client.put(
        f"/leave-types/{other['id']}",
        json={"name": "sick", "yearly_quota": 10, "is_paid": True, "is_active": True},
        headers=admin,
    )
    assert clash.status_code == 409

    missing = client.put(
        "/leave-types/999",
        json={"name": "Nope", "yearly_quota": 1, "is_paid": True, "is_active": True},
        headers=admin,
    )
    assert missing.status_code == 404


# ---------- Holidays ----------

def test_holidays_start_empty(client, admin):
    assert client.get("/holidays", headers=admin).json() == []


def test_create_and_list_sorted_by_date(client, admin):
    client.post("/holidays", json={"name": "Christmas", "date": "2026-12-25"}, headers=admin)
    client.post("/holidays", json={"name": "Independence Day", "date": "2026-08-14"}, headers=admin)
    r = client.get("/holidays", headers=admin).json()
    assert [h["date"] for h in r] == ["2026-08-14", "2026-12-25"]


def test_filter_by_year(client, admin):
    client.post("/holidays", json={"name": "New Year", "date": "2026-01-01"}, headers=admin)
    client.post("/holidays", json={"name": "New Year", "date": "2027-01-01"}, headers=admin)
    assert len(client.get("/holidays?year=2026", headers=admin).json()) == 1
    assert len(client.get("/holidays?year=2027", headers=admin).json()) == 1
    assert len(client.get("/holidays", headers=admin).json()) == 2


def test_duplicate_date_is_409(client, admin):
    client.post("/holidays", json={"name": "A Day", "date": "2026-03-23"}, headers=admin)
    r = client.post("/holidays", json={"name": "Another", "date": "2026-03-23"}, headers=admin)
    assert r.status_code == 409


def test_bad_holiday_input_rejected(client, admin):
    assert client.post("/holidays", json={"name": "Bad", "date": "not-a-date"}, headers=admin).status_code == 422
    assert client.post("/holidays", json={"name": "Bad", "date": "2026-13-45"}, headers=admin).status_code == 422
    assert client.post("/holidays", json={"name": "Bad", "date": "0026-05-01"}, headers=admin).status_code == 422
    assert client.post("/holidays", json={"name": "  ", "date": "2026-05-01"}, headers=admin).status_code == 422
    assert client.post("/holidays", json={"date": "2026-05-01"}, headers=admin).status_code == 422


def test_holiday_permissions(client, admin, hr, emp):
    created = client.post("/holidays", json={"name": "Labour Day", "date": "2026-05-01"}, headers=admin).json()
    body = {"name": "Labour Day", "date": "2026-05-02"}

    assert client.get("/holidays", headers=hr).status_code == 200
    assert client.post("/holidays", json=body, headers=hr).status_code == 403
    assert client.put(f"/holidays/{created['id']}", json=body, headers=hr).status_code == 403
    assert client.delete(f"/holidays/{created['id']}", headers=hr).status_code == 403

    assert client.get("/holidays", headers=emp).status_code == 403
    assert client.delete(f"/holidays/{created['id']}", headers=emp).status_code == 403

    assert client.get("/holidays").status_code == 401


def test_update_and_delete_holiday(client, admin):
    created = client.post("/holidays", json={"name": "Labour Day", "date": "2026-05-01"}, headers=admin).json()

    r = client.put(
        f"/holidays/{created['id']}",
        json={"name": "Workers Day", "date": "2026-05-01"},
        headers=admin,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Workers Day"

    assert client.delete(f"/holidays/{created['id']}", headers=admin).status_code == 204
    assert client.get("/holidays", headers=admin).json() == []
    assert client.delete(f"/holidays/{created['id']}", headers=admin).status_code == 404


def test_update_holiday_to_a_taken_date_is_409(client, admin):
    client.post("/holidays", json={"name": "A", "date": "2026-05-01"}, headers=admin)
    other = client.post("/holidays", json={"name": "B", "date": "2026-05-02"}, headers=admin).json()
    r = client.put(f"/holidays/{other['id']}", json={"name": "B", "date": "2026-05-01"}, headers=admin)
    assert r.status_code == 409