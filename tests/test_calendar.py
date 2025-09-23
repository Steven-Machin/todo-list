import app


TASKS_FIXTURE = [
    {
        "text": "My Task",
        "priority": "High",
        "assigned_username": "nick",
        "due_date": "2024-01-02",
    },
    {
        "text": "Team Task",
        "priority": "Low",
        "assigned_username": "liz",
        "due_date": "2024-01-03",
    },
    {
        "text": "Bad Task",
        "priority": "Medium",
        "assigned_username": "nick",
        "due_date": "invalid-date",
    },
]

SHIFT_FIXTURE = [
    {
        "date": "2024-01-04",
        "start_time": "08:00",
        "end_time": "17:00",
        "assigned_to": "nick",
    },
    {
        "date": "2024-01-05",
        "start_time": "09:00",
        "end_time": "17:00",
        "assigned_to": "liz",
    },
    {
        "date": "bad-date",
        "start_time": "10:00",
        "end_time": "18:00",
        "assigned_to": "nick",
    },
]

USERS_FIXTURE = [
    {"username": "nick", "display_name": "Nick"},
    {"username": "liz", "display_name": "Liz"},
]


def patch_calendar_sources(monkeypatch):
    monkeypatch.setattr(app, "load_tasks", lambda: TASKS_FIXTURE)
    monkeypatch.setattr(app, "load_shifts", lambda: SHIFT_FIXTURE)
    monkeypatch.setattr(app, "load_users", lambda: USERS_FIXTURE)


def test_calendar_member_scope_guard(monkeypatch):
    patch_calendar_sources(monkeypatch)

    with app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "nick"
            sess["role"] = "member"

        resp_my = client.get("/api/calendar?scope=my")
        assert resp_my.status_code == 200
        data_my = resp_my.get_json()
        assert len(data_my) == 2  # one task, one shift
        titles_my = {evt["title"] for evt in data_my}
        assert "My Task (High)" in titles_my
        assert "Shift 08:00 - 17:00" in titles_my

        resp_all = client.get("/api/calendar?scope=all")
        assert resp_all.status_code == 200
        data_all = resp_all.get_json()
        assert data_all == data_my  # non-manager cannot escalate scope

        titles = {evt["title"] for evt in data_all}
        assert "Team Task (Low)" not in titles
        assert all("Bad Task" not in title for title in titles)


def test_calendar_manager_all_scope(monkeypatch):
    patch_calendar_sources(monkeypatch)

    with app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "steven"
            sess["role"] = "manager"

        resp = client.get("/api/calendar?scope=all")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 4  # two tasks + two shifts, invalid entries skipped
        titles = {evt["title"] for evt in data}
        assert "My Task (High)" in titles
        assert "Team Task (Low)" in titles
        assert "Shift 08:00 - 17:00" in titles
        assert "Shift 09:00 - 17:00" in titles
        assert all("Bad Task" not in title for title in titles)
