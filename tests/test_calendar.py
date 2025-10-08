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
        "id": "shift-nick",
        "date": "2024-01-04",
        "start_time": "08:00",
        "end_time": "17:00",
        "assigned_to": "nick",
    },
    {
        "id": "shift-liz",
        "date": "2024-01-05",
        "start_time": "09:00",
        "end_time": "17:00",
        "assigned_to": "liz",
    },
    {
        "id": "shift-bad",
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
    monkeypatch.setattr(app, "load_shift_attendance_store", lambda: {})


def login_client(client, username: str, role: str):
    with client.session_transaction() as sess:
        sess["_user_id"] = username
        sess["_fresh"] = True
        sess["username"] = username
        sess["role"] = role


def test_calendar_member_scope_guard(monkeypatch):
    patch_calendar_sources(monkeypatch)

    with app.app.test_client() as client:
        login_client(client, "nick", "member")

        resp_my = client.get("/api/calendar?scope=my")
        assert resp_my.status_code == 200
        data_my = resp_my.get_json()
        assert len(data_my) == 2  # one task, one shift

        task_event = next(evt for evt in data_my if evt["extendedProps"]["type"] == "task")
        assert task_event["title"] == "My Task"
        assert task_event["start"] == "2024-01-02"
        assert task_event["allDay"] is True

        shift_event = next(evt for evt in data_my if evt["extendedProps"]["type"] == "shift")
        assert shift_event["title"] == "Shift 08:00 - 17:00"

        resp_all = client.get("/api/calendar?scope=all")
        assert resp_all.status_code == 200
        data_all = resp_all.get_json()
        assert data_all == data_my  # non-manager cannot escalate scope

        titles = {evt["title"] for evt in data_all}
        assert "Team Task" not in titles
        assert all("Bad Task" not in title for title in titles)


def test_calendar_manager_all_scope(monkeypatch):
    patch_calendar_sources(monkeypatch)

    with app.app.test_client() as client:
        login_client(client, "steven", "manager")

        resp = client.get("/api/calendar?scope=all")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 4  # two tasks + two shifts, invalid entries skipped
        titles = {evt["title"] for evt in data}
        assert "My Task" in titles
        assert "Team Task" in titles
        assert "Shift 08:00 - 17:00" in titles
        assert "Shift 09:00 - 17:00" in titles
        assert all("Bad Task" not in title for title in titles)


def test_calendar_page_renders(monkeypatch):
    patch_calendar_sources(monkeypatch)

    with app.app.test_client() as client:
        login_client(client, "nick", "member")
        response = client.get("/calendar")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Task Calendar" in body


def test_calendar_shift_attendance_props(monkeypatch):
    patch_calendar_sources(monkeypatch)
    attendance_store = {
        "nick": {
            "shift-nick": {
                "status": "attended",
                "recorded_at": "2024-01-04T18:15",
            }
        }
    }
    monkeypatch.setattr(app, "load_shift_attendance_store", lambda: attendance_store)

    with app.app.test_client() as client:
        login_client(client, "nick", "member")
        response = client.get("/api/calendar?scope=my")
        assert response.status_code == 200
        data = response.get_json()
        shift_event = next(evt for evt in data if evt["extendedProps"]["type"] == "shift")
        props = shift_event["extendedProps"]
        assert props["attendance_status"] == "attended"
        assert props["attendance_recorded_at"] == "2024-01-04T18:15"
