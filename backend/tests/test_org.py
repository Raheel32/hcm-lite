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


@pytest.mark.parametrize("path", ["/departments", "/designations"])
class TestOrgItems:
    def test_starts_empty(self, client, admin, path):
        r = client.get(path, headers=admin)
        assert r.status_code == 200
        assert r.json() == []

    def test_admin_can_create(self, client, admin, path):
        r = client.post(path, json={"name": "Engineering"}, headers=admin)
        assert r.status_code == 201
        assert r.json()["name"] == "Engineering"
        assert r.json()["is_active"] is True
        assert len(client.get(path, headers=admin).json()) == 1

    def test_name_is_cleaned(self, client, admin, path):
        r = client.post(path, json={"name": "  Human   Resources "}, headers=admin)
        assert r.json()["name"] == "Human Resources"

    def test_duplicate_ignores_case(self, client, admin, path):
        client.post(path, json={"name": "Sales"}, headers=admin)
        r = client.post(path, json={"name": "sALES"}, headers=admin)
        assert r.status_code == 409

    def test_bad_names_rejected(self, client, admin, path):
        assert client.post(path, json={"name": "   "}, headers=admin).status_code == 422
        assert client.post(path, json={"name": "A"}, headers=admin).status_code == 422
        assert client.post(path, json={"name": "x" * 101}, headers=admin).status_code == 422
        assert client.post(path, json={}, headers=admin).status_code == 422

    def test_hr_can_view_but_not_change(self, client, admin, hr, path):
        created = client.post(path, json={"name": "Finance"}, headers=admin).json()
        assert client.get(path, headers=hr).status_code == 200
        assert client.post(path, json={"name": "Legal"}, headers=hr).status_code == 403
        r = client.put(f"{path}/{created['id']}", json={"name": "X Y", "is_active": True}, headers=hr)
        assert r.status_code == 403

    def test_employee_has_no_access(self, client, emp, path):
        assert client.get(path, headers=emp).status_code == 403
        assert client.post(path, json={"name": "Legal"}, headers=emp).status_code == 403

    def test_no_token_is_401(self, client, path):
        assert client.get(path).status_code == 401
        assert client.post(path, json={"name": "Legal"}).status_code == 401

    def test_rename(self, client, admin, path):
        item = client.post(path, json={"name": "Ops"}, headers=admin).json()
        r = client.put(f"{path}/{item['id']}", json={"name": "Operations", "is_active": True}, headers=admin)
        assert r.status_code == 200
        assert r.json()["name"] == "Operations"

    def test_saving_without_changing_name_is_ok(self, client, admin, path):
        item = client.post(path, json={"name": "Ops"}, headers=admin).json()
        r = client.put(f"{path}/{item['id']}", json={"name": "Ops", "is_active": True}, headers=admin)
        assert r.status_code == 200

    def test_rename_to_existing_name_is_409(self, client, admin, path):
        client.post(path, json={"name": "Sales"}, headers=admin)
        other = client.post(path, json={"name": "Support"}, headers=admin).json()
        r = client.put(f"{path}/{other['id']}", json={"name": "Sales", "is_active": True}, headers=admin)
        assert r.status_code == 409

    def test_deactivate_hides_from_default_list(self, client, admin, path):
        item = client.post(path, json={"name": "Old Team"}, headers=admin).json()
        client.put(f"{path}/{item['id']}", json={"name": "Old Team", "is_active": False}, headers=admin)
        assert client.get(path, headers=admin).json() == []
        shown = client.get(f"{path}?include_inactive=true", headers=admin).json()
        assert len(shown) == 1
        assert shown[0]["is_active"] is False

    def test_unknown_id_is_404(self, client, admin, path):
        r = client.put(f"{path}/999", json={"name": "Nope", "is_active": True}, headers=admin)
        assert r.status_code == 404