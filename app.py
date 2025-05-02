from flask import Flask, render_template, request, redirect, url_for, flash, session
import json
import os
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "manager-task-secret"
TASKS_FILE = "tasks.json"
USERS_FILE = "users.json"
SHIFTS_FILE = "shifts.json"

def load_tasks():
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "r") as file:
            return json.load(file)
    return []

def save_tasks(tasks):
    with open(TASKS_FILE, "w") as file:
        json.dump(tasks, file, indent=2)

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as file:
            return json.load(file)
    return []

def save_users(users):
    with open(USERS_FILE, "w") as file:
        json.dump(users, file, indent=2)

def sort_tasks(task_list, key='due'):
    priority_order = {"High": 0, "Medium": 1, "Low": 2}

    def task_sort_key(task):
        due_date = task.get("due") or "9999-12-31"
        done = task.get("done", False)
        priority = priority_order.get(task.get("priority", "Medium"), 1)
        created = task.get("created_at") or "9999-12-31T23:59"

        if key == 'priority':
            return priority
        elif key == 'created':
            return datetime.strptime(created, "%Y-%m-%dT%H:%M")
        elif key == 'status':
            return done
        return datetime.strptime(due_date, "%Y-%m-%d")

    return sorted(task_list, key=task_sort_key)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "username" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def load_shifts():
    if os.path.exists(SHIFTS_FILE):
        with open(SHIFTS_FILE, "r") as file:
            return json.load(file)
    return []

def save_shifts(shifts):
    with open(SHIFTS_FILE, "w") as file:
        json.dump(shifts, file, indent=2)

tasks = load_tasks()

@app.route("/")
@login_required
def index():
    username = session["username"]
    role = session.get("role", "member")
    assignee_filter = request.args.get("assignee", "All")
    sort_by = request.args.get("sort", "due")
    today = datetime.today().date()

    users = load_users()
    assignable_users = sorted([u["username"] for u in users if u.get("role") != "manager"])

    for task in tasks:
        if "due" in task and task["due"]:
            try:
                due_date = datetime.strptime(task["due"], "%Y-%m-%d").date()
                task["overdue"] = not task.get("done") and due_date < today
            except ValueError:
                task["overdue"] = False
        else:
            task["overdue"] = False

    if role == "manager":
        visible_tasks = tasks
    else:
        visible_tasks = [t for t in tasks if t.get("assigned_to", "").lower() == username.lower()]

    assignees = sorted({task["assigned_to"] for task in tasks if task.get("assigned_to")})
    filtered_tasks = [t for t in visible_tasks if assignee_filter == "All" or t.get("assigned_to") == assignee_filter]
    sorted_filtered_tasks = sort_tasks(filtered_tasks, key=sort_by)

    total_tasks = len(filtered_tasks)
    completed_tasks = len([t for t in filtered_tasks if t.get("done")])
    remaining_tasks = total_tasks - completed_tasks

    if role == "member":
        all_shifts = load_shifts()
        upcoming = [
            s for s in all_shifts
            if s["assigned_to"].lower() == username.lower()
            and datetime.strptime(s["date"], "%Y-%m-%d").date() >= today
    ]
        upcoming.sort(key=lambda s: s["date"])
        next_shift = upcoming[0] if upcoming else None
    else:
        next_shift = None



    return render_template(
        "index.html",
        tasks=sorted_filtered_tasks,
        app_name="To Do List",
        assignees=assignees,
        assignee_filter=assignee_filter,
        sort_by=sort_by,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        remaining_tasks=remaining_tasks,
        role=role,
        current_user=username,
        assignable_users=assignable_users
        , next_shift=next_shift,
    )

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]
        users = load_users()
        if any(u["username"] == username for u in users):
            flash("Username already exists.")
            return redirect(url_for("signup"))
        hashed = generate_password_hash(password)
        users.append({"username": username, "password": hashed, "role": "member"})
        save_users(users)
        flash("Account created. Please log in.")
        return redirect(url_for("login"))
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip().lower()
        password = request.form["password"]
        users = load_users()
        user = next((u for u in users if u["username"] == username), None)
        if user and check_password_hash(user["password"], password):
            session["username"] = username
            session["role"] = user.get("role", "member")
            flash("Logged in successfully.")
            return redirect(url_for("index"))
        flash("Invalid username or password.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("username", None)
    session.pop("role", None)
    flash("You have been logged out.")
    return redirect(url_for("login"))

@app.route("/add", methods=["POST"])
@login_required
def add():
    text = request.form.get("task", "").strip()
    priority = request.form.get("priority", "Medium")
    due = request.form.get("due", "").strip()
    notes = request.form.get("notes", "").strip()
    created_by = session["username"]
    created_at = datetime.now().strftime("%Y-%m-%dT%H:%M")
    role = session["role"]
    username = session["username"]

    if role == "manager":
        assigned_to = request.form.get("assigned_to", "").strip().lower()
    else:
        assigned_to = username

    if text:
        task = {
            "text": text,
            "done": False,
            "priority": priority,
            "assigned_to": assigned_to,
            "due": due,
            "notes": notes,
            "created_by": created_by,
            "created_at": created_at
        }
        tasks.append(task)
        save_tasks(tasks)
        flash("Task added successfully.")
    return redirect("/")

@app.route("/toggle/<int:task_id>")
@login_required
def toggle(task_id):
    username = session["username"]
    role = session["role"]
    if 0 <= task_id < len(tasks):
        if role == "manager" or tasks[task_id].get("assigned_to") == username:
            tasks[task_id]["done"] = not tasks[task_id]["done"]
            save_tasks(tasks)
            flash("Task status updated.")
    return redirect("/")

@app.route("/remove/<int:task_id>")
@login_required
def remove(task_id):
    username = session["username"]
    role = session["role"]
    if 0 <= task_id < len(tasks):
        if role == "manager" or tasks[task_id].get("assigned_to") == username:
            tasks.pop(task_id)
            save_tasks(tasks)
            flash("Task removed.")
    return redirect("/")


@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
@login_required
def edit(task_id):
    username = session["username"]
    role = session["role"]
    if request.method == "POST":
        text = request.form.get("task", "").strip()
        priority = request.form.get("priority", "Medium")
        assigned_to = request.form.get("assigned_to", "").strip().lower()
        due = request.form.get("due", "").strip()
        notes = request.form.get("notes", "").strip()

        if text and 0 <= task_id < len(tasks):
            if role == "manager" or tasks[task_id].get("assigned_to") == username:
                tasks[task_id]["text"] = text
                tasks[task_id]["priority"] = priority
                tasks[task_id]["assigned_to"] = assigned_to if role == "manager" else username
                tasks[task_id]["due"] = due
                tasks[task_id]["notes"] = notes
                save_tasks(tasks)
                flash("Task updated.")
        return redirect("/")
    else:
        if 0 <= task_id < len(tasks):
            if role == "manager" or tasks[task_id].get("assigned_to") == username:
                return render_template("edit.html", task=tasks[task_id], task_id=task_id, role=role)
    return redirect("/")

@app.route("/shifts")
@login_required
def view_shifts():
    if session.get("role") != "manager":
        return redirect("/")
    shifts = load_shifts()
    return render_template("shifts.html", shifts=shifts)

@app.route("/shifts/add", methods=["GET", "POST"])
@login_required
def add_shift():
    if session.get("role") != "manager":
        return redirect("/")

    if request.method == "POST":
        date = request.form.get("date")
        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")
        assigned_to = request.form.get("assigned_to", "").strip().lower()
        notes = request.form.get("notes", "")

        if date and start_time and end_time and assigned_to:
            shifts = load_shifts()
            shifts.append({
                "date": date,
                "start_time": start_time,
                "end_time": end_time,
                "assigned_to": assigned_to,
                "notes": notes
            })
            save_shifts(shifts)
            flash("Shift added successfully.")
            return redirect(url_for("view_shifts"))
        else:
            flash("All fields except notes are required.")

    users = load_users()
    employees = sorted([u["username"] for u in users if u.get("role") != "manager"])
    return render_template("add_shift.html", employees=employees)

@app.route("/my-shifts")
@login_required
def my_shifts():
    username = session.get("username")
    shifts = load_shifts()
    user_shifts = [s for s in shifts if s["assigned_to"].lower() == username.lower()]
    return render_template("my_shifts.html", shifts=user_shifts)


if __name__ == "__main__":
    app.run(debug=True)

