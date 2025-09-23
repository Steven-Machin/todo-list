import pytest

import app


USERS_FIXTURE = [
    {"username": "nick", "display_name": "Nick"},
    {"username": "liz", "display_name": "Liz"},
]


@pytest.mark.parametrize(
    "task, expected",
    [
        ({"assigned_to": "Nick"}, True),
        ({"assigned_username": "nick"}, True),
    ],
)
def test_assigned_to_me_variants(task, expected):
    assert app.assigned_to_me(task, "nick", USERS_FIXTURE) is expected


def test_overdue_member_filters_and_handles_bad_dates(monkeypatch):
    tasks = [
        {"text": "Legacy Display", "done": False, "assigned_to": "Nick", "due_date": "2000-01-01"},
        {"text": "Username Task", "done": False, "assigned_username": "nick", "due": "2000-01-02T08:00"},
        {"text": "Other Task", "done": False, "assigned_username": "liz", "due_date": "2000-01-03"},
        {"text": "Bad Date", "done": False, "assigned_to": "Nick", "due_date": "not-a-date"},
        {"text": "Completed", "done": True, "assigned_to": "Nick", "due_date": "1999-01-01"},
        {"text": "Future", "done": False, "assigned_to": "Nick", "due_date": "2999-01-01"},
    ]
    monkeypatch.setattr(app, "load_tasks", lambda: tasks)
    monkeypatch.setattr(app, "load_users", lambda: USERS_FIXTURE)

    with app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "nick"
            sess["role"] = "member"

        response = client.get("/overdue")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Legacy Display" in body
        assert "Username Task" in body
        assert "Other Task" not in body
        assert "Bad Date" not in body


def test_overdue_manager_sees_all_valid(monkeypatch):
    tasks = [
        {"text": "Legacy Display", "done": False, "assigned_to": "Nick", "due_date": "2000-01-01"},
        {"text": "Username Task", "done": False, "assigned_username": "nick", "due": "2000-01-02T08:00"},
        {"text": "Other Task", "done": False, "assigned_username": "liz", "due_date": "2000-01-03"},
        {"text": "Bad Date", "done": False, "assigned_to": "Nick", "due_date": "not-a-date"},
    ]
    monkeypatch.setattr(app, "load_tasks", lambda: tasks)
    monkeypatch.setattr(app, "load_users", lambda: USERS_FIXTURE)

    with app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "steven"
            sess["role"] = "manager"

        response = client.get("/overdue")
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Legacy Display" in body
        assert "Username Task" in body
        assert "Other Task" in body
        assert "Bad Date" not in body
