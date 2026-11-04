import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user, require_roles
from app.core.security import create_access_token
from app.database import get_db
from app.models.user import Role

# A tiny app with one route per rule, just for testing the guards.
guard_app = FastAPI()


@guard_app.get("/admin-only")
def admin_only(user=Depends(require_roles(Role.admin))):
    return {"ok": True}


@guard_app.get("/staff")
def staff(user=Depends(require_roles(Role.admin, Role.hr))):
    return {"ok": True}


@guard_app.get("/any-logged-in")
def any_logged_in(user=Depends(get_current_user)):
    return {"ok": True}


@pytest.fixture()
def guard_client(db_session):
    def override_get_db():
        yield db_session

    guard_app.dependency_overrides[get_db] = override_get_db
    yield TestClient(guard_app)
    guard_app.dependency_overrides.clear()


def auth(user):
    token = create_access_token(user.id, user.role.value)
    return {"Authorization": f"Bearer {token}"}


def test_no_token_is_401(guard_client):
    assert guard_client.get("/admin-only").status_code == 401
    assert guard_client.get("/staff").status_code == 401


def test_admin_only_route(guard_client, make_user):
    admin = make_user(email="a@t.com", role=Role.admin)
    hr = make_user(email="h@t.com", role=Role.hr)
    emp = make_user(email="e@t.com", role=Role.employee)

    assert guard_client.get("/admin-only", headers=auth(admin)).status_code == 200
    assert guard_client.get("/admin-only", headers=auth(hr)).status_code == 403
    assert guard_client.get("/admin-only", headers=auth(emp)).status_code == 403


def test_admin_or_hr_route(guard_client, make_user):
    admin = make_user(email="a@t.com", role=Role.admin)
    hr = make_user(email="h@t.com", role=Role.hr)
    emp = make_user(email="e@t.com", role=Role.employee)

    assert guard_client.get("/staff", headers=auth(admin)).status_code == 200
    assert guard_client.get("/staff", headers=auth(hr)).status_code == 200
    assert guard_client.get("/staff", headers=auth(emp)).status_code == 403


def test_temporary_password_blocks_everything_else(guard_client, make_user):
    user = make_user(role=Role.hr, must_change_password=True)
    assert guard_client.get("/any-logged-in", headers=auth(user)).status_code == 403
    assert guard_client.get("/staff", headers=auth(user)).status_code == 403


def test_deactivating_a_user_kills_their_token(guard_client, make_user, db_session):
    user = make_user(role=Role.admin)
    headers = auth(user)
    assert guard_client.get("/admin-only", headers=headers).status_code == 200

    user.is_active = False
    db_session.commit()
    assert guard_client.get("/admin-only", headers=headers).status_code == 401


def test_role_comes_from_database_not_from_token(guard_client, make_user, db_session):
    user = make_user(role=Role.admin)
    headers = auth(user)  # this token says "admin"
    assert guard_client.get("/admin-only", headers=headers).status_code == 200

    user.role = Role.employee  # demoted in the database
    db_session.commit()
    assert guard_client.get("/admin-only", headers=headers).status_code == 403