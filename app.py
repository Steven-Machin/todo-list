from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import json
import os
import uuid
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "manager-task-secret"
TASKS_FILE = "tasks.json"
USERS_FILE = "users.json"
SHIFTS_FILE = "shifts.json"
TITLES_FILE = "titles.json"
GROUPS_FILE      = "group_chats.json"
GROUP_TASKS_FILE = "group_tasks.json"
UPLOAD_FOLDER    = "static/uploads"
ALLOWED_EXTS     = {"png","jpg","jpeg","gif"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

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

def load_titles():
    if os.path.exists(TITLES_FILE):
        with open(TITLES_FILE, "r") as file:
            return json.load(file)
    return []

def save_titles(titles):
    with open(TITLES_FILE, "w") as file:
        json.dump(titles, file, indent=2)

def allowed_file(fn):
    return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_EXTS

def load_groups():
    if os.path.exists(GROUPS_FILE):
        return json.load(open(GROUPS_FILE))
    return []

def save_groups(gs):
    json.dump(gs, open(GROUPS_FILE,"w"), indent=2)

def load_group_tasks():
    if os.path.exists(GROUP_TASKS_FILE):
        return json.load(open(GROUP_TASKS_FILE))
    return {}

def save_group_tasks(gts):
    json.dump(gts, open(GROUP_TASKS_FILE,"w"), indent=2)

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
@app.template_filter('format_datetime')
def format_datetime(value):
    # parse the ISO string, then format
    dt = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    return dt.strftime("%b %d, %Y %I:%M %p")  # e.g. "May 02, 2025 08:35 PM"

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        raw_username = request.form["username"].strip()
        username_lower = raw_username.lower()
        username_title = raw_username.title()
        password = request.form["password"]
        users = load_users()
        if any(u["username"] == username_lower for u in users):
            flash("Username already exists.")
            return redirect(url_for("signup"))
        hashed = generate_password_hash(password)
        users.append({
            "username": username_lower,
            "display_name": username_title,
            "password": hashed,
            "role": "member"
        })
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
    due_date    = request.form.get("due_date", "").strip()     # renamed
    due_time    = request.form.get("due_time", "").strip()     # new
    recurring   = request.form.get("recurring") == "weekly"    # new checkbox
    notes = request.form.get("notes", "").strip()
    created_by = session["username"]
    created_at = datetime.now().strftime("%Y-%m-%dT%H:%M")
    role = session["role"]
    username = session["username"]
    users = load_users()

    if role == "manager":
        assigned_username = request.form.get("assigned_to", "").strip().lower()
        assigned_user = next((u for u in users if u["username"] == assigned_username), None)
        assigned_to = assigned_user["display_name"] if assigned_user and "display_name" in assigned_user else assigned_username.title()
    else:
        assigned_user = next((u for u in users if u["username"] == username), None)
        assigned_to = assigned_user["display_name"] if assigned_user and "display_name" in assigned_user else username.title()

    if text:
        task = {
            "text": text,
            "done": False,
            "priority": priority,
            "assigned_to": assigned_to,
            "due_date": due_date,            # YYYY-MM-DD or ""
            "due_time": due_time or None,    # "HH:MM" or None
            "recurring": "weekly" if recurring else None,
            "notes": notes,
            "created_by": created_by,
            "created_at": datetime.now().strftime("%Y-%m-%dT%H:%M")
        }
        tasks.append(task)
        save_tasks(tasks)
        flash("Task added successfully.")
    return redirect("/")

from datetime import datetime, timedelta

@app.route("/toggle/<int:task_id>", methods=["POST"])
@login_required
def toggle(task_id):
    username = session["username"]
    role     = session["role"]

    if 0 <= task_id < len(tasks):
        t = tasks[task_id]
        if role == "manager" or t.get("assigned_to", "").lower() == username.lower():
            t["done"] = not t["done"]

            if t["done"]:
                # 12-hour clock, e.g. "2025-05-02T02:34 PM"
                now = datetime.now().strftime("%Y-%m-%dT%I:%M %p")
                t["completed_at"] = now

                # if weekly recurring, schedule next week
                if t.get("recurring") == "weekly" and t.get("due_date"):
                    dd = (datetime.strptime(t["due_date"], "%Y-%m-%d").date()
                          + timedelta(weeks=1))
                    new = t.copy()
                    new.update({
                        "done": False,
                        "due_date": dd.strftime("%Y-%m-%d"),
                        "completed_at": None
                    })
                    tasks.append(new)
            else:
                t.pop("completed_at", None)

            save_tasks(tasks)
            flash("Task status updated.")

    return redirect(url_for("tasks_page"))




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

@app.route("/tasks")
@login_required
def tasks_page():
    username = session["username"]
    role     = session.get("role", "member")
    sort_by  = request.args.get("sort", "due_asc")
    today    = datetime.today().date()

    users = load_users()

    # 1) Pick only the tasks this user should see
    visible = tasks if role == "manager" else [
        t for t in tasks
        if t.get("assigned_to","").lower() == username.lower()
    ]

    # 2) If “completed” was selected, show only done tasks
    if sort_by == "completed":
        visible = [t for t in visible if t.get("done")]

    # 3) Sort according to sort_by
    def sort_key(t):
        # due date, fallback far future
        dd = t.get("due") or "9999-12-31"
        pd = {"High":0,"Medium":1,"Low":2}[t.get("priority","Medium")]
        return {
            "due_asc":    datetime.strptime(dd, "%Y-%m-%d"),
            "due_desc":  -datetime.strptime(dd, "%Y-%m-%d").timestamp(),
            "priority_hl": pd,
            "priority_lh": -pd,
            "completed": datetime.strptime(t.get("completed_at","9999-12-31T00:00"), "%Y-%m-%dT%H:%M")
        }[sort_by]

    visible.sort(key=sort_key)

    # 4) Figure out who we can assign new tasks to
    eligible = {
        "assistant manager","family swim supervisor","lead supervisor",
        "swim administrator","programming supervisor","supervisor"
    }
    if role == "manager":
        assignable_users = [
            u for u in users
            if u.get("role")!="manager"
            and any(tlt.lower() in eligible for tlt in u.get("titles",[]))
        ]
    else:
        assignable_users = []

    return render_template(
      "task_manager.html",
      tasks=visible,
      role=role,
      sort_by=sort_by,
      assignable_users=assignable_users
    )

@app.route("/tasks/create")
@login_required
def create_task_page():
    # only managers can create
    if session.get("role") != "manager":
        return redirect(url_for("tasks_page"))

    users = load_users()
    eligible = {
      "assistant manager","family swim supervisor","lead supervisor",
      "swim administrator","programming supervisor","supervisor"
    }
    assignable = [
      u for u in users
      if u.get("role")!="manager"
         and any(t.lower() in eligible for t in u.get("titles",[]))
    ]

    return render_template("create_task.html",
                       assignable_users=assignable)

@app.route("/groups")
@login_required
def group_chat_manager():
    if session.get("role") != "manager":
        return redirect("/")
    return render_template("group_chat_manager.html")

@app.route("/members")
@login_required
def team_member_manager():
    if session.get("role") != "manager":
        return redirect("/")
    users = load_users()
    return render_template("team_manager.html", users=users)

@app.route("/titles", methods=["GET", "POST"])
@login_required
def title_manager():
    if session.get("role") != "manager":
        return redirect("/")

    users = load_users()
    titles = load_titles()

    # Handle new title creation
    if request.method == "POST":
        new_title = request.form.get("new_title", "").strip()
        if new_title and new_title not in titles:
            titles.append(new_title)
            save_titles(titles)
            flash(f"Title '{new_title}' added.")

    # Categorize users by titles
    categorized = {"Untitled": []}
    for user in users:
        user["display_name"] = user["username"].capitalize()
        if user.get("titles"):
            for t in user["titles"]:
                categorized.setdefault(t, []).append(user)
        else:
            categorized["Untitled"].append(user)

    return render_template("title_manager.html", categorized=categorized, all_titles=titles)


@app.route("/titles/update", methods=["POST"])
@login_required
def update_titles():
    if session.get("role") != "manager":
        return redirect("/")

    users = load_users()
    for user in users:
        uname = user["username"]

        # 1) Handle “add title” form
        add_key = f"add_title_{uname}"
        if add_key in request.form:
            new_title = request.form[add_key].strip()
            if new_title:
                # initialize if missing
                user.setdefault("titles", [])
                # only append if not already present
                if new_title not in user["titles"]:
                    user["titles"].append(new_title)

        # 2) Handle “remove title” links
        rem_key = f"remove_title_{uname}"
        if rem_key in request.form:
            rem = request.form[rem_key]
            user["titles"] = [t for t in user.get("titles", []) if t != rem]

    save_users(users)
    flash("Titles updated.")
    return redirect(url_for("title_manager"))

@app.route("/calendar")
@login_required
def calendar_view():
    # only managers need the full calendar; members could be redirected or shown just their days
    return render_template("calendar.html", role=session["role"])

@app.route("/api/tasks/events")
@login_required
def task_events():
    # produce a simple list of events FullCalendar can consume
    evts = []
    for idx, t in enumerate(load_tasks()):
        if t.get("due"):
            evts.append({
                "id": idx,
                "title": f"{t['text']} ({t['priority']})",
                "start": t["due"],
                # FullCalendar can color‐code based on custom data
                "color": {"High":"#f66","Medium":"#fa6","Low":"#6a6"}[t["priority"]],
                "extendedProps": {
                  "assigned_to": t["assigned_to"],
                  "notes": t.get("notes",""),
                  "done": t.get("done",False)
                }
            })
    return jsonify(evts)

@app.route("/settings")
@login_required
def settings():
    # you can restrict to managers if you like:
    # if session.get("role") != "manager":
    #     return redirect("/")
    return render_template("settings.html")

@app.route("/overdue")
@login_required
def overdue_tasks():
    username = session["username"]
    role = session.get("role", "member")
    today = datetime.today().date()

    all_tasks = load_tasks()
    # manager sees everything, others only their own
    visible = all_tasks if role=="manager" else [
        t for t in all_tasks
        if t.get("assigned_to","").lower() == username.lower()
    ]

    # find the overdue ones
    overdue = []
    for idx, t in enumerate(visible):
        due = t.get("due") or t.get("due_date")
        try:
            due_date = datetime.strptime(due, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if not t.get("done") and due_date < today:
            overdue.append((idx, t))

    return render_template("overdue.html",
                            overdue=overdue,
                            role=role)

@app.route("/groups/add", methods=["POST"])
@login_required
def add_group():
    if session.get("role")!="manager":
        return redirect("/")
    name = request.form.get("group_name","").strip()
    sup  = request.form.get("supervisor","").strip().lower()
    if name and sup:
        gs = load_groups()
        new = {
          "id": str(uuid.uuid4()),
          "name": name,
          "supervisor": sup,
          "members": [sup]
        }
        gs.append(new)
        save_groups(gs)
        flash(f"Group '{name}' created.")
    return redirect(url_for("group_chat_manager"))

@app.route("/groups/<group_id>")
@login_required
def view_group(group_id):
    if session.get("role")!="manager":
        return redirect("/")
    groups = load_groups()
    g = next((g for g in groups if g["id"]==group_id), None)
    if not g:
        flash("Group not found."); return redirect("/groups")

    # load tasks mapping
    all_tasks = load_group_tasks().get(group_id, [])
    users = load_users()
    # supervisor can add; members can complete
    return render_template("group_chat.html",
        group=g, users=users, tasks=all_tasks
    )

@app.route("/groups/<group_id>/add_member", methods=["POST"])
@login_required
def add_group_member(group_id):
    gs = load_groups()
    g = next((g for g in gs if g["id"]==group_id), None)
    if g and session.get("role")=="manager":
        u = request.form.get("member","").strip().lower()
        if u and u not in g["members"]:
            g["members"].append(u)
            save_groups(gs)
            flash("Member added.")
    return redirect(url_for("view_group",group_id=group_id))

@app.route("/groups/<group_id>/remove_member", methods=["POST"])
@login_required
def remove_group_member(group_id):
    gs = load_groups()
    g = next((g for g in gs if g["id"]==group_id), None)
    if g and session.get("role")=="manager":
        u = request.form.get("member","")
        if u in g["members"] and u!=g["supervisor"]:
            g["members"].remove(u)
            save_groups(gs)
            flash("Member removed.")
    return redirect(url_for("view_group",group_id=group_id))

@app.route("/groups/<group_id>/add_task", methods=["POST"])
@login_required
def add_group_task(group_id):
    gts = load_group_tasks()
    gtasks = gts.setdefault(group_id, [])
    text      = request.form.get("text","").strip()
    priority  = request.form.get("priority","Medium")
    due_date  = request.form.get("due_date","").strip()
    recurring = request.form.get("recurring")=="weekly"
    notes     = request.form.get("notes","").strip()
    creator   = session["username"]
    if text:
        t = {
          "text": text,
          "priority": priority,
          "due_date": due_date,
          "recurring": "weekly" if recurring else None,
          "notes": notes,
          "done": False,
          "created_by": creator,
          "created_at": datetime.now().isoformat(timespec="minutes")
        }
        gtasks.append(t)
        save_group_tasks(gts)
        flash("Task added.")
    return redirect(url_for("view_group",group_id=group_id))

@app.route("/groups/<group_id>/toggle_task/<int:idx>", methods=["POST"])
@login_required
def toggle_group_task(group_id, idx):
    gts = load_group_tasks()
    tasks = gts.get(group_id, [])
    if 0<=idx<len(tasks):
        t = tasks[idx]
        user = session["username"]
        # only group members may toggle
        grp = next(g for g in load_groups() if g["id"]==group_id)
        if user in grp["members"]:
            t["done"] = not t["done"]
            now = datetime.now().strftime("%Y-%m-%dT%H:%M")
            if t["done"]:
                t["completed_at"] = now
                t["completed_by"] = user
                # handle photo upload
                if "photo" in request.files:
                    f = request.files["photo"]
                    if f and allowed_file(f.filename):
                        fn = secure_filename(f"{uuid.uuid4()}_{f.filename}")
                        out = os.path.join(app.config["UPLOAD_FOLDER"], fn)
                        f.save(out)
                        t["photo"] = fn
                # schedule next if recurring
                if t.get("recurring")=="weekly" and t.get("due_date"):
                    dd = datetime.fromisoformat(t["due_date"]).date()
                    nxt = dd + timedelta(weeks=1)
                    new = t.copy()
                    new.update({k:None for k in ("done","completed_at","completed_by","photo")})
                    new["due_date"] = nxt.isoformat()
                    tasks.append(new)
            else:
                for k in ("completed_at","completed_by","photo"):
                    t.pop(k,None)
            save_group_tasks(gts)
            flash("Status updated.")
    return redirect(url_for("view_group",group_id=group_id))


if __name__ == "__main__":
    app.run(debug=True)

