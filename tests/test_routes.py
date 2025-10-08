import app


def _login(client, monkeypatch, username="manager", role="manager"):
    record = {
        "username": username,
        "password": app.generate_password_hash("secret"),
        "role": role,
        "display_name": username.title(),
    }

    def fake_find(user_id):
        return record if user_id == username else None

    monkeypatch.setattr(app, "find_user_record", fake_find)

    with client.session_transaction() as sess:
        sess["_user_id"] = username
        sess["_fresh"] = True
        sess["username"] = username
        sess["role"] = role

    return record


def test_login_page_renders():
    with app.app.test_client() as client:
        response = client.get("/login")
        assert response.status_code == 200
        assert "Log In" in response.get_data(as_text=True)


def test_login_success_redirects(monkeypatch):
    record = {
        "username": "nick",
        "password": app.generate_password_hash("password123"),
        "role": "member",
        "display_name": "Nick",
    }
    monkeypatch.setattr(app, "find_user_record", lambda uname: record if uname == "nick" else None)

    with app.app.test_client() as client:
        response = client.post(
            "/login",
            data={"username": "nick", "password": "password123"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")


def test_login_invalid_credentials(monkeypatch):
    monkeypatch.setattr(app, "find_user_record", lambda _: None)

    with app.app.test_client() as client:
        response = client.post("/login", data={"username": "ghost", "password": "nope"})
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "Invalid credentials." in body


def test_add_task_manager_flow(monkeypatch):
    tasks_store: list[dict] = []

    monkeypatch.setattr(app, "load_tasks", lambda: list(tasks_store))
    def _save(updated):
        tasks_store.clear()
        tasks_store.extend(updated)

    monkeypatch.setattr(app, "save_tasks", _save)

    manager_record = {
        "username": "manager",
        "password": "",
        "role": "manager",
        "display_name": "Manager",
    }
    monkeypatch.setattr(app, "load_users", lambda: [manager_record])
    monkeypatch.setattr(app, "find_user_record", lambda uname: manager_record if uname == "manager" else None)

    with app.app.test_client() as client:
        _login(client, monkeypatch)
        response = client.post(
            "/add",
            data={"task": "Write integration tests", "priority": "High"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert tasks_store, "Task should be persisted"
    assert tasks_store[-1]["text"] == "Write integration tests"


def test_shift_submission_invokes_creator(monkeypatch):
    created = {}
    monkeypatch.setattr(app, "create_shift_for_user", lambda user, date, start, end, notes: created.update({
        "user": user,
        "date": date,
        "start": start,
        "end": end,
        "notes": notes,
    }))
    monkeypatch.setattr(app, "load_shifts_for_user", lambda _: [])
    monkeypatch.setattr(app, "find_user_record", lambda uname: {
        "username": uname,
        "password": "",
        "role": "member",
        "display_name": uname.title(),
    })

    with app.app.test_client() as client:
        _login(client, monkeypatch, username="member", role="member")
        response = client.post(
            "/shifts",
            data={
                "date": "2025-01-05",
                "start_time": "08:00",
                "end_time": "12:00",
                "notes": "Coverage",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert created["user"] == "member"
    assert created["date"] == "2025-01-05"


def test_system_check_accessible_for_manager(monkeypatch):
    monkeypatch.setattr(app, "load_tasks", lambda: [])
    with app.app.test_client() as client:
        _login(client, monkeypatch)
        response = client.get("/system-check")
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "System Check" in body
        assert "Dashboard" in body


def test_system_check_forbidden_for_members(monkeypatch):
    monkeypatch.setattr(app, "load_tasks", lambda: [])
    with app.app.test_client() as client:
        _login(client, monkeypatch, username="member", role="member")
        response = client.get("/system-check")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")


def test_tasks_page_renders_with_breadcrumb(monkeypatch):
    manager_record = {
        "username": "manager",
        "password": "",
        "role": "manager",
        "display_name": "Manager",
    }
    monkeypatch.setattr(app, "find_user_record", lambda uname: manager_record if uname == "manager" else None)
    monkeypatch.setattr(app, "load_users", lambda: [manager_record])
    monkeypatch.setattr(app, "load_tasks", lambda: [])

    with app.app.test_client() as client:
        _login(client, monkeypatch)
        response = client.get("/tasks")
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "breadcrumb" in body
