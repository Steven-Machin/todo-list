from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import json, os
import uuid
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "manager-task-secret"

# ──────── File paths & config ────────
TASKS_FILE           = "tasks.json"
USERS_FILE           = "users.json"
SHIFTS_FILE          = "shifts.json"
TITLES_FILE          = "titles.json"
GROUPS_FILE          = "group_chats.json"
GROUP_TASKS_FILE     = "group_tasks.json"
GROUP_MESSAGES_FILE  = "group_messages.json"
UPLOAD_FOLDER        = "static/uploads"
ALLOWED_EXTS         = {"png","jpg","jpeg","gif"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ──────── Helpers ────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def allowed_file(fn):
    return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_EXTS

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ──────── JSON wrappers ────────
def load_tasks():          return load_json(TASKS_FILE, [])
def save_tasks(t):         save_json(TASKS_FILE, t)
def load_users():          return load_json(USERS_FILE, [])
def save_users(u):         save_json(USERS_FILE, u)
def load_shifts():         return load_json(SHIFTS_FILE, [])
def save_shifts(s):        save_json(SHIFTS_FILE, s)
def load_titles():         return load_json(TITLES_FILE, [])
def save_titles(t):        save_json(TITLES_FILE, t)
def load_groups():         return load_json(GROUPS_FILE, [])
def save_groups(g):        save_json(GROUPS_FILE, g)
def load_group_tasks():    return load_json(GROUP_TASKS_FILE, {})
def save_group_tasks(g):   save_json(GROUP_TASKS_FILE, g)
def load_group_messages(): return load_json(GROUP_MESSAGES_FILE, {})
def save_group_messages(m):save_json(GROUP_MESSAGES_FILE, m)

# ──────── Jinja filter ────────
@app.template_filter('format_datetime')
def format_datetime(value):
    dt = datetime.fromisoformat(value)
    return dt.strftime("%b %d, %Y %I:%M %p")

# ──────── Auth ────────
@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        raw = request.form["username"].strip()
        uname = raw.lower()
        users = load_users()
        if any(u["username"] == uname for u in users):
            flash("Username already exists.")
            return redirect(url_for("signup"))
        users.append({
            "username": uname,
            "display_name": raw.title(),
            "password": generate_password_hash(request.form["password"]),
            "role": "member",
            "titles": []
        })
        save_users(users)
        flash("Account created; please log in.")
        return redirect(url_for("login"))
    return render_template("signup.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        uname = request.form["username"].strip().lower()
        pwd = request.form["password"]
        users = load_users()
        user = next((u for u in users if u["username"] == uname), None)
        if user and check_password_hash(user["password"], pwd):
            session["username"] = uname
            session["role"] = user.get("role","member")
            flash("Logged in.")
            return redirect(url_for("index"))
        flash("Invalid credentials.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))

# ──────── Home / Dashboard ────────

@app.route("/")
@login_required
def index():
    username = session["username"]
    role     = session.get("role", "member")
    today    = datetime.today().date()

    users = load_users()
    tasks = load_tasks()

    # mark overdue
    for t in tasks:
        d = t.get("due") or t.get("due_date")
        try:
            due_d = datetime.strptime(d, "%Y-%m-%d").date()
            t["overdue"] = not t.get("done", False) and due_d < today
        except:
            t["overdue"] = False

    # filter which tasks this user should see
    visible = tasks if role == "manager" else [
        t for t in tasks
        if t.get("assigned_to", "").lower() == username.lower()
    ]

    assignees = sorted({t["assigned_to"] for t in tasks if t.get("assigned_to")})
    groups   = load_groups()             # returns list of {id, name, ...}
    all_msgs = load_group_messages()     # returns dict: group_id → [ {sender, timestamp, text}, … ]

    # map group_id → name for quick lookup
    name_by_id = {g["id"]: g["name"] for g in groups}
    # map username → display_name for quick lookup
    disp_by_user = {u["username"]: u["display_name"] for u in users}

    flat = []
    for gid, msgs in all_msgs.items():
        for m in msgs:
            flat.append({
                "group_id":    gid,
                "group_name":  name_by_id.get(gid, gid),
                "sender":      m["sender"],
                "sender_disp": disp_by_user.get(m["sender"], m["sender"].title()),
                "text":        m["text"],
                "timestamp":   m["timestamp"],
            })

    # sort descending by timestamp, then take the top 5
    latest_chats = sorted(
        flat,
        key=lambda x: x["timestamp"],
        reverse=True
    )[:5]
    # ─────────────────────────────────────────────────────────────────────────

    return render_template(
        "index.html",
        tasks=visible,
        assignees=assignees,
        role=role,
        latest_chats=latest_chats
    )

# ──────── Task CRUD & Toggle ────────
@app.route("/add", methods=["POST"])
@login_required
def add():
    text = request.form.get("task","").strip()
    if not text:
        flash("Task text required.")
        return redirect(url_for("tasks_page"))

    priority = request.form.get("priority","Medium")
    assigned_username = request.form.get("assigned_to","").strip().lower()
    due_date = request.form.get("due_date","").strip()
    recurring = "weekly" if request.form.get("recurring")=="weekly" else None
    notes = request.form.get("notes","").strip()
    created_by = session["username"]

    users = load_users()
    disp = next((u["display_name"] for u in users if u["username"]==assigned_username), assigned_username.title())

    new = {
        "text": text,
        "done": False,
        "priority": priority,
        "assigned_to": disp,
        "due_date": due_date,
        "recurring": recurring,
        "notes": notes,
        "created_by": created_by,
        "created_at": datetime.now().isoformat(timespec="minutes")
    }
    ts = load_tasks()
    ts.append(new)
    save_tasks(ts)
    flash("Task added.")
    return redirect(url_for("tasks_page"))

@app.route("/toggle/<int:task_id>", methods=["POST"])
@login_required
def toggle(task_id):
    username = session["username"]
    role = session["role"]
    ts = load_tasks()
    if 0<=task_id<len(ts):
        t = ts[task_id]
        if role=="manager" or t.get("assigned_to","").lower()==username.lower():
            t["done"] = not t.get("done",False)
            if t["done"]:
                t["completed_at"] = datetime.now().isoformat(timespec="minutes")
                if t.get("recurring")=="weekly" and t.get("due_date"):
                    dd = datetime.strptime(t["due_date"],"%Y-%m-%d").date()
                    nxt = dd + timedelta(weeks=1)
                    new = {**t, "done":False, "due_date":nxt.isoformat()}
                    new.pop("completed_at",None)
                    ts.append(new)
            else:
                t.pop("completed_at",None)
    save_tasks(ts)
    flash("Task status updated.")
    return redirect(url_for("tasks_page"))

@app.route("/remove/<int:task_id>")
@login_required
def remove(task_id):
    username = session["username"]
    role = session["role"]
    ts = load_tasks()
    if 0<=task_id<len(ts):
        if role=="manager" or ts[task_id].get("assigned_to","").lower()==username.lower():
            ts.pop(task_id)
            save_tasks(ts)
            flash("Task removed.")
    return redirect(url_for("tasks_page"))

@app.route("/edit/<int:task_id>", methods=["GET","POST"])
@login_required
def edit(task_id):
    username = session["username"]
    role = session["role"]
    ts = load_tasks()

    if request.method=="POST":
        if 0<=task_id<len(ts):
            t = ts[task_id]
            if role=="manager" or t.get("assigned_to","").lower()==username.lower():
                t["text"] = request.form.get("task","").strip()
                t["priority"] = request.form.get("priority","Medium")
                t["assigned_to"] = request.form.get("assigned_to","").strip().title() if role=="manager" else t["assigned_to"]
                t["due_date"] = request.form.get("due_date","").strip()
                t["notes"] = request.form.get("notes","").strip()
                save_tasks(ts)
                flash("Task updated.")
        return redirect(url_for("tasks_page"))
    else:
        if 0<=task_id<len(ts):
            t = ts[task_id]
            if role=="manager" or t.get("assigned_to","").lower()==username.lower():
                users = load_users()
                return render_template("edit.html", task=t, task_id=task_id, assignable_users=[u["username"] for u in users if u.get("role")!="manager"])
    return redirect(url_for("tasks_page"))

# ──────── Shifts ────────
@app.route("/shifts")
@login_required
def view_shifts():
    if session.get("role")!="manager":
        return redirect(url_for("index"))
    return render_template("shifts.html", shifts=load_shifts())

@app.route("/shifts/add", methods=["GET","POST"])
@login_required
def add_shift():
    if session.get("role")!="manager":
        return redirect(url_for("view_shifts"))
    if request.method=="POST":
        d = request.form.get("date")
        s = request.form.get("start_time")
        e = request.form.get("end_time")
        a = request.form.get("assigned_to","").strip().lower()
        if d and s and e and a:
            sh = load_shifts()
            sh.append({"date":d,"start_time":s,"end_time":e,"assigned_to":a,"notes":request.form.get("notes","")})
            save_shifts(sh)
            flash("Shift added.")
            return redirect(url_for("view_shifts"))
    users = load_users()
    return render_template("add_shift.html", employees=[u["username"] for u in users if u.get("role")!="manager"])

@app.route("/my-shifts")
@login_required
def my_shifts():
    u = session["username"]
    sh = [s for s in load_shifts() if s["assigned_to"].lower()==u.lower()]
    return render_template("my_shifts.html", shifts=sh)

# ──────── Task Manager pages ────────
@app.route("/tasks")
@login_required
def tasks_page():
    username = session["username"]
    role     = session.get("role","member")
    sort_by  = request.args.get("sort","due_asc")

    ts = load_tasks()
    if role!="manager":
        ts = [t for t in ts if t.get("assigned_to","").lower()==username.lower()]

    if sort_by=="completed":
        ts = [t for t in ts if t.get("done")]

    def keyfn(t):
        dd = t.get("due") or t.get("due_date") or "9999-12-31"
        pd = {"High":0,"Medium":1,"Low":2}[t.get("priority","Medium")]
        return {
            "due_asc":    datetime.strptime(dd,"%Y-%m-%d"),
            "due_desc":  -datetime.strptime(dd,"%Y-%m-%d").timestamp(),
            "priority_hl": pd,
            "priority_lh": -pd,
            "completed":  datetime.fromisoformat(t.get("completed_at","9999-12-31T00:00"))
        }[sort_by]

    ts.sort(key=keyfn)

    users = load_users()
    elig = {"assistant manager","family swim supervisor","lead supervisor","swim administrator","programming supervisor","supervisor"}
    assignable = [u["username"] for u in users if u["role"]!="manager" and any(t.lower() in elig for t in u.get("titles",[]))]

    return render_template("task_manager.html",
                           tasks=ts,
                           role=role,
                           sort_by=sort_by,
                           assignable_users=assignable)

@app.route("/tasks/create", methods=["GET","POST"])
@login_required
def create_task_page():
    if session.get("role")!="manager":
        return redirect(url_for("tasks_page"))
    if request.method=="POST":
        return redirect(url_for("add"))
    users = load_users()
    elig = {"assistant manager","family swim supervisor","lead supervisor","swim administrator","programming supervisor","supervisor"}
    assignable = [u["username"] for u in users if u["role"]!="manager" and any(t.lower() in elig for t in u.get("titles",[]))]
    return render_template("create_task.html", assignable_users=assignable)

# ──────── Team Member Manager ────────
@app.route("/members")
@login_required
def team_member_manager():
    if session.get("role")!="manager":
        return redirect(url_for("index"))
    return render_template("team_manager.html", users=load_users())

@app.route("/titles", methods=["GET","POST"])
@login_required
def title_manager():
    if session.get("role")!="manager":
        return redirect(url_for("index"))

    users = load_users()
    titles = load_titles()
    if request.method=="POST":
        nt = request.form.get("new_title","").strip()
        if nt and nt not in titles:
            titles.append(nt)
            save_titles(titles)
            flash(f"Title '{nt}' added.")

    categorized = {"Untitled":[]}
    for u in users:
        u.setdefault("titles",[])
        u["display_name"] = u["username"].capitalize()
        if u["titles"]:
            for t in u["titles"]:
                categorized.setdefault(t,[]).append(u)
        else:
            categorized["Untitled"].append(u)

    return render_template("title_manager.html",
                           categorized=categorized,
                           all_titles=titles)

@app.route("/titles/update", methods=["POST"])
@login_required
def update_titles():
    if session.get("role")!="manager":
        return redirect(url_for("title_manager"))
    users = load_users()
    for u in users:
        add_key = f"add_title_{u['username']}"
        if add_key in request.form:
            nt = request.form[add_key].strip()
            if nt and nt not in u.setdefault("titles",[]):
                u["titles"].append(nt)

        rem_key = f"remove_title_{u['username']}"
        if rem_key in request.form:
            rem = request.form[rem_key]
            u["titles"] = [t for t in u.get("titles",[]) if t!=rem]

    save_users(users)
    flash("Titles updated.")
    return redirect(url_for("title_manager"))

# ──────── Calendar ────────
@app.route("/calendar")
@login_required
def calendar_view():
    return render_template("calendar.html", role=session.get("role","member"))

@app.route("/api/tasks/events")
@login_required
def task_events():
    evts=[]
    for i,t in enumerate(load_tasks()):
        if t.get("due"):
            evts.append({
                "id": i,
                "title": f"{t['text']} ({t['priority']})",
                "start": t["due"],
                "color": {"High":"#f66","Medium":"#fa6","Low":"#6a6"}[t["priority"]],
                "extendedProps": {
                    "assigned_to":t["assigned_to"],
                    "notes":t.get("notes",""),
                    "done":t.get("done",False)
                }
            })
    return jsonify(evts)

# ──────── Settings ────────
@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html")

# ──────── Overdue ────────
@app.route("/overdue")
@login_required
def overdue_tasks():
    username = session["username"]
    role     = session.get("role","member")
    today    = datetime.today().date()
    ts = load_tasks()
    if role!="manager":
        ts=[t for t in ts if t.get("assigned_to","").lower()==username.lower()]

    overdue=[]
    for i,t in enumerate(ts):
        due = t.get("due") or t.get("due_date")
        try:
            d = datetime.strptime(due,"%Y-%m-%d").date()
            if not t.get("done") and d<today:
                overdue.append((i,t))
        except:
            pass
    return render_template("overdue.html", overdue=overdue, role=role)

# ──────── Group Chat Manager ────────
@app.route("/groups")
@login_required
def group_chat_manager():
    if session.get("role")!="manager":
        return redirect(url_for("index"))
    groups = load_groups()
    users  = load_users()
    # only those with a “Supervisor” title
    supervisors = [
        u for u in users
        if any(t.lower()=="supervisor" for t in u.get("titles",[]))
    ]
    return render_template("group_chat_manager.html",
                           groups=groups,
                           supervisors=supervisors,
                           users=users)

@app.route("/groups/add", methods=["POST"])
@login_required
def add_group():
    if session.get("role")!="manager":
        return redirect(url_for("group_chat_manager"))
    name = request.form.get("group_name","").strip()
    sup  = request.form.get("supervisor","").strip().lower()
    if not name or not sup:
        flash("Both name & supervisor required.")
        return redirect(url_for("group_chat_manager"))
    gs = load_groups()
    new = {"id":str(uuid.uuid4()),"name":name,"supervisor":sup,"members":[sup]}
    gs.append(new); save_groups(gs)
    flash("Group created.")
    return redirect(url_for("group_chat_manager"))

@app.route("/groups/<group_id>")
@login_required
def view_group(group_id):
    user = session["username"]
    gs   = load_groups()
    grp  = next((g for g in gs if g["id"]==group_id), None)
    if not grp or (session.get("role")!="manager" and user not in grp["members"]):
        return redirect(url_for("group_chat_manager"))

    tasks    = load_group_tasks().get(group_id,[])
    messages = load_group_messages().get(group_id,[])
    users    = load_users()
    return render_template("group_chat.html",
                           group=grp,
                           users=users,
                           tasks=tasks,
                           messages=messages)

@app.route("/groups/<group_id>/message", methods=["POST"])
@login_required
def post_group_message(group_id):
    text = request.form.get("message","").strip()
    if text:
        msgs = load_group_messages()
        ch   = msgs.setdefault(group_id,[])
        ch.append({
            "sender": session["username"],
            "timestamp": datetime.now().isoformat(timespec="minutes"),
            "text": text
        })
        save_group_messages(msgs)
    return redirect(url_for("view_group", group_id=group_id))

@app.route("/groups/<group_id>/add_member", methods=["POST"])
@login_required
def add_group_member(group_id):
    if session.get("role")!="manager":
        return redirect(url_for("group_chat_manager"))
    gs = load_groups()
    grp = next((g for g in gs if g["id"]==group_id), None)
    if grp:
        u = request.form.get("member","").strip().lower()
        if u and u not in grp["members"]:
            grp["members"].append(u)
            save_groups(gs)
            flash("Member added.")
    return redirect(url_for("view_group",group_id=group_id))

@app.route("/groups/<group_id>/remove_member", methods=["POST"])
@login_required
def remove_group_member(group_id):
    if session.get("role")!="manager":
        return redirect(url_for("group_chat_manager"))
    gs = load_groups()
    grp = next((g for g in gs if g["id"]==group_id), None)
    if grp:
        u = request.form.get("member","").strip().lower()
        if u in grp["members"] and u!=grp["supervisor"]:
            grp["members"].remove(u)
            save_groups(gs)
            flash("Member removed.")
    return redirect(url_for("view_group",group_id=group_id))

@app.route("/groups/<group_id>/add_task", methods=["POST"])
@login_required
def add_group_task(group_id):
    text     = request.form.get("text","").strip()
    priority = request.form.get("priority","Medium")
    notes    = request.form.get("notes","").strip()
    if not text:
        flash("Task text required.")
        return redirect(url_for("view_group",group_id=group_id))

    gts = load_group_tasks()
    ch  = gts.setdefault(group_id,[])
    ch.append({
        "text": text,
        "priority": priority,
        "recurring": "weekly",
        "notes": notes,
        "done": False,
        "created_by": session["username"],
        "created_at": datetime.now().isoformat(timespec="minutes")
    })
    save_group_tasks(gts)
    flash("Task added.")
    return redirect(url_for("view_group",group_id=group_id))

@app.route("/groups/<group_id>/toggle_task/<int:idx>", methods=["POST"])
@login_required
def toggle_group_task(group_id, idx):
    user = session["username"]
    gs   = load_groups()
    grp  = next((g for g in gs if g["id"]==group_id), None)
    if not grp or user not in grp["members"]:
        flash("Not authorized.")
        return redirect(url_for("view_group",group_id=group_id))

    gts = load_group_tasks()
    lst = gts.get(group_id,[])
    if 0<=idx<len(lst):
        t = lst[idx]
        t["done"] = not t.get("done",False)
        if t["done"]:
            now = datetime.now()
            t["completed_at"] = now.isoformat(timespec="minutes")
            t["completed_by"] = user
            f = request.files.get("photo")
            if f and allowed_file(f.filename):
                fn = secure_filename(f"{uuid.uuid4()}_{f.filename}")
                f.save(os.path.join(app.config["UPLOAD_FOLDER"],fn))
                t["photo"] = fn
            # schedule next week
            nxt = now + timedelta(weeks=1)
            lst.append({
                "text": t["text"],
                "priority": t["priority"],
                "recurring": "weekly",
                "notes": t.get("notes",""),
                "done": False,
                "created_by": t["created_by"],
                "created_at": nxt.isoformat(timespec="minutes")
            })
        else:
            for k in ("completed_at","completed_by","photo"):
                t.pop(k,None)
    save_group_tasks(gts)
    flash("Status updated.")
    return redirect(url_for("view_group",group_id=group_id))

if __name__ == "__main__":
    app.run(debug=True)
