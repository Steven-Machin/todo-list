import app


def test_update_shift_attendance_cycle(monkeypatch):
    shift_records = [
        {
            "id": "shift-1",
            "assigned_to": "nick",
            "date": "2024-01-04",
            "start_time": "08:00",
            "end_time": "12:00",
            "notes": "",
        }
    ]
    attendance_store = {}

    monkeypatch.setattr(app, "load_shifts", lambda: shift_records)
    monkeypatch.setattr(app, "load_shift_attendance_store", lambda: attendance_store)

    def save_store(updated):
        attendance_store.clear()
        attendance_store.update(updated)

    monkeypatch.setattr(app, "save_shift_attendance_store", save_store)

    result = app.update_shift_attendance("nick", "shift-1", "attended")
    assert result["status"] == "attended"
    assert "nick" in attendance_store
    assert "shift-1" in attendance_store["nick"]

    app.update_shift_attendance("nick", "shift-1", "clear")
    assert attendance_store == {}


def test_update_shift_attendance_rejects_invalid_status(monkeypatch):
    shift_records = [
        {
            "id": "shift-2",
            "assigned_to": "nick",
            "date": "2024-01-05",
            "start_time": "09:00",
            "end_time": "13:00",
            "notes": "",
        }
    ]

    monkeypatch.setattr(app, "load_shifts", lambda: shift_records)
    monkeypatch.setattr(app, "load_shift_attendance_store", lambda: {})
    monkeypatch.setattr(app, "save_shift_attendance_store", lambda store: None)

    try:
        app.update_shift_attendance("nick", "shift-2", "unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for invalid status")
