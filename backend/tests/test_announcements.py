from datetime import date, datetime

import pytest

from app.core import clock
from app.core.security import create_access_token
from app.models.comms import Announcement
from app.models.user import Role

# "Now" is 2026-11-05.


def auth(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value)}"}


def set_now(monkeypatch, text):
    moment = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=clock.office_tz())
    monkeypatch.setattr(clock, "now", lambda: moment)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    set_now(monkeypatch, "2026-11-05 10:00")


@pytest.fixture()
def admin_user(make_user):
    return make_user(email="admin@t.com", role=Role.admin)


@pytest.fixture()
def admin(admin_user):
    return auth(admin_user)


@pytest.fixture()
def hr_user(make_user):
    return make_user(email="hr@t.com", role=Role.hr)


@pytest.fixture()
def hr(hr_user):
    return auth(hr_user)


@pytest.fixture()
def emp(make_user):
    return auth(make_user(email="emp@t.com", role=Role.employee))


def post(client, headers, **over):
    body = {"title": "Office closed", "body": "The office is closed on Friday.", "expires_on": None}
    body.update(over)
    return client.post("/announcements", json=body, headers=headers)


def titles(client, headers, query=""):
    r = client.get(f"/announcements{query}", headers=headers)
    assert r.status_code == 200
    return [item["title"] for item in r.json()["items"]]


# ---------- Posting ----------

def test_hr_and_admin_can_post_employees_cannot(client, admin, hr, emp):
    for headers, role, email in ((hr, "hr", "hr@t.com"), (admin, "admin", "admin@t.com")):
        r = post(client, headers)
        assert r.status_code == 201
        body = r.json()
        assert body["title"] == "Office closed"
        assert body["posted_by"] == {"email": email, "role": role}
        assert body["expires_on"] is None
        assert body["is_active"] is True
        assert body["created_at"] is not None
    assert post(client, emp).status_code == 403
    assert client.post("/announcements", json={"title": "Hello", "body": "World"}).status_code == 401


def test_text_is_cleaned(client, hr):
    r = post(client, hr, title="  Office   closed ", body="  Line one\n\nLine two  ")
    assert r.json()["title"] == "Office closed"
    assert r.json()["body"] == "Line one\n\nLine two"


@pytest.mark.parametrize(
    "over",
    [
        {"title": "ab"},
        {"title": "   "},
        {"title": "x" * 151},
        {"body": ""},
        {"body": "  "},
        {"body": "x" * 2001},
        {"expires_on": "garbage"},
    ],
)
def test_bad_input_is_422(client, hr, over):
    assert post(client, hr, **over).status_code == 422


def test_missing_fields_are_422(client, hr):
    assert client.post("/announcements", json={"body": "Something"}, headers=hr).status_code == 422
    assert client.post("/announcements", json={"title": "Something"}, headers=hr).status_code == 422


def test_expiry_date_limits(client, hr):
    assert post(client, hr, expires_on="2026-11-04").status_code == 400   # yesterday
    assert post(client, hr, expires_on="2026-11-05").status_code == 201   # today still counts
    assert post(client, hr, expires_on="2027-11-05").status_code == 201   # exactly a year ahead
    assert post(client, hr, expires_on="2027-11-06").status_code == 400


# ---------- Reading ----------

def test_everyone_sees_active_announcements_newest_first(client, admin, hr, emp, admin_user, db_session):
    post(client, hr, title="First news")
    post(client, admin, title="Second news")
    post(client, hr, title="Ends today", expires_on="2026-11-05")
    db_session.add(Announcement(
        title="Old news", body="This one has expired.", expires_on=date(2026, 11, 4), created_by_id=admin_user.id,
    ))
    db_session.commit()

    for headers in (admin, hr, emp):
        assert titles(client, headers) == ["Ends today", "Second news", "First news"]

    # Staff can look at expired ones, employees cannot
    everything = client.get("/announcements?include_expired=true", headers=hr).json()
    assert [i["title"] for i in everything["items"]] == ["Old news", "Ends today", "Second news", "First news"]
    assert everything["items"][0]["is_active"] is False
    assert everything["items"][1]["is_active"] is True
    assert "Old news" not in titles(client, emp, "?include_expired=true")

    page = client.get("/announcements?limit=2&offset=2", headers=emp).json()
    assert len(page["items"]) == 1 and page["total"] == 3

    for bad in ("limit=0", "limit=1000", "offset=-1"):
        assert client.get(f"/announcements?{bad}", headers=emp).status_code == 422
    assert client.get("/announcements").status_code == 401


# ---------- Deleting ----------

def test_who_can_delete(client, admin, hr, emp, make_user):
    other_hr = auth(make_user(email="hr2@t.com", role=Role.hr))
    mine = post(client, hr, title="HR post").json()
    theirs = post(client, other_hr, title="Other HR post").json()

    assert client.delete(f"/announcements/{mine['id']}", headers=emp).status_code == 403
    assert client.delete(f"/announcements/{mine['id']}").status_code == 401
    assert client.delete(f"/announcements/{theirs['id']}", headers=hr).status_code == 403   # not theirs
    assert client.delete(f"/announcements/{mine['id']}", headers=hr).status_code == 204     # their own
    assert client.delete(f"/announcements/{theirs['id']}", headers=admin).status_code == 204  # Admin may delete any
    assert client.delete(f"/announcements/{mine['id']}", headers=admin).status_code == 404
    assert client.delete("/announcements/999", headers=admin).status_code == 404
    assert titles(client, emp) == []


def test_posting_and_deleting_are_audited(client, admin, hr):
    item = post(client, hr, title="Eid holiday", expires_on="2026-12-01").json()
    client.delete(f"/announcements/{item['id']}", headers=hr)

    entries = client.get("/audit?entity_type=announcement", headers=admin).json()["items"]
    assert [e["action"] for e in entries] == ["announcement.delete", "announcement.create"]
    assert entries[1]["after"] == {"title": "Eid holiday", "expires_on": "2026-12-01"}
    assert entries[0]["before"] == {"title": "Eid holiday", "expires_on": "2026-12-01"}
    assert all(e["actor"]["email"] == "hr@t.com" for e in entries)