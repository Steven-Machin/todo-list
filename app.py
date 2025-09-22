# app.py
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify, abort
)
import json, os, uuid
from datetime import datetime, timedelta, date
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "manager-task-secret"

# ─────────────────────────────── Paths / Config ───────────────────────────────
TASKS_FILE            = "tasks.json"
USERS_FILE            = "users.json"
SHIFTS_FILE           = "shifts.json"
TITLES_FILE           = "titles.json"

GROUPS_FILE           = "group_chats.json"
GROUP_TASKS_FILE      = "group_tasks.json"
GROUP_MESSAGES_FILE   = "group_messages.json"
GROUP_SEEN_FILE       = "group_seen.json"

REMINDERS_FILE        = "reminders.json"
PREFERENCES_FILE      = "preferences.json"
PASSWORD_RESETS_FILE  = "password_resets.json"   # forgot/reset flow

UPLOAD_FOLDER         = "static/uploads"
ALLOWED_EXTS          = {"png","jpg","jpeg","gif"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# ─────────────────────────────── Utilities ───────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                # corrupted file fallback
                return default
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
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

def manager_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "manager":
            flash("Managers only.")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper

@app.context_processor
def inject_user_ctx():
    return {
        "current_user": session.get("username"),
        "current_role": session.get("role", "member")
    }

def _norm(s):
    return (s or "").strip().lower()


def parse_dt_any(s: str) -> datetime | None:
    """Return a naive datetime for common ISO-like strings or None."""
    if not s:
        return None
    text = s.strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None

    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def iso_date(d: date) -> str:
    return d.isoformat()


def iso_minutes(dt: datetime) -> str:
    return dt.replace(second=0, microsecond=0).isoformat(timespec="minutes")


def parse_date(date_str):
    """
    Parse just a DATE (YYYY-MM-DD) from a variety of inputs.
    Returns date or None.
    """
    dt = parse_dt_any(date_str)
    return dt.date() if dt else None

def parse_date_any(date_str, default_far=True):
    """
    Return a datetime for sorting purposes.
    If default_far is True and parse fails, returns a 'far future' date to push to end.
    """
    d = parse_date(date_str)
    if d:
        return datetime.combine(d, datetime.min.time())
    return datetime(9999, 12, 31) if default_far else datetime(1900, 1, 1)

def assigned_to_me(task, username, users=None):
    """
    True if task assignment matches current user by:
      - task['assigned_username'] equals username (case-insensitive)
      - OR task['assigned_to'] equals user's display_name (legacy, case-insensitive)
    """
    if users is None:
        users = load_users()

    task_username = _norm(task.get("assigned_username"))
    if task_username and task_username == _norm(username):
        return True

    assignee_display = _norm(task.get("assigned_to"))
    if not assignee_display:
        return False

    u = next((u for u in users if _norm(u.get("username")) == _norm(username)), None)
    display_name = _norm(u.get("display_name")) if u else ""
    return bool(display_name and assignee_display == display_name)

# ─────────────────────────────── JSON wrappers ───────────────────────────────
def load_tasks():             return load_json(TASKS_FILE, [])
def save_tasks(t):            save_json(TASKS_FILE, t)

def load_users():             return load_json(USERS_FILE, [])
def save_users(u):            save_json(USERS_FILE, u)

def load_shifts():            return load_json(SHIFTS_FILE, [])
def save_shifts(s):           save_json(SHIFTS_FILE, s)

def load_titles():            return load_json(TITLES_FILE, [])
def save_titles(t):           save_json(TITLES_FILE, t)

def load_groups():            return load_json(GROUPS_FILE, [])
def save_groups(g):           save_json(GROUPS_FILE, g)

def load_group_tasks():       return load_json(GROUP_TASKS_FILE, {})
def save_group_tasks(g):      save_json(GROUP_TASKS_FILE, g)

def load_group_messages():    return load_json(GROUP_MESSAGES_FILE, {})
def save_group_messages(m):   save_json(GROUP_MESSAGES_FILE, m)

def load_group_seen():        return load_json(GROUP_SEEN_FILE, {})
def save_group_seen(d):       save_json(GROUP_SEEN_FILE, d)

def load_reminders():         return load_json(REMINDERS_FILE, [])
def save_reminders(items):    save_json(REMINDERS_FILE, items)

def load_prefs():             return load_json(PREFERENCES_FILE, {})
def save_prefs(p):            save_json(PREFERENCES_FILE, p)

def load_resets():            return load_json(PASSWORD_RESETS_FILE, {})
def save_resets(data):        save_json(PASSWORD_RESETS_FILE, data)

# ─────────────────────────────── Jinja filters ───────────────────────────────
@app.template_filter('format_datetime')
def format_datetime(value):
    """Accept ISO 'YYYY-MM-DDTHH:MM' or 12h 'YYYY-MM-DDT%I:%M %p' and format nicely."""
    if not value:
        return ""
    dt = None
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        try:
            dt = datetime.strptime(value, "%Y-%m-%dT%I:%M %p")
        except Exception:
            return value
    return dt.strftime("%b %d, %Y %I:%M %p")

# ─────────────────────────────── Auth ───────────────────────────────
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
    # The "Are you sure?" confirmation is implemented in templates via onclick confirm()
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))

# ─────────────────────────────── Forgot / Reset Password ─────────────────────
@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        uname = request.form.get("username", "").strip().lower()
        users = load_users()
        user = next((u for u in users if u["username"] == uname), None)

        # Always pretend success to avoid user enumeration
        if not user:
            flash("If that account exists, a reset link was created.")
            return render_template("forgot_sent.html", reset_url=None)

        tok = uuid.uuid4().hex
        expires = (datetime.now() + timedelta(minutes=60)).isoformat(timespec="minutes")
        resets = load_resets()
        resets[tok] = {"username": uname, "expires": expires}
        save_resets(resets)

        reset_url = url_for("reset_password", token=tok, _external=False)
        flash("Use the link below to reset your password.")
        return render_template("forgot_sent.html", reset_url=reset_url)

    return render_template("forgot.html")

@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    resets = load_resets()
    rec = resets.get(token)
    if not rec:
        flash("Invalid or expired reset link.")
        return redirect(url_for("login"))

    try:
        exp = datetime.fromisoformat(rec["expires"])
        if datetime.now() > exp:
            resets.pop(token, None)
            save_resets(resets)
            flash("Reset link has expired.")
            return redirect(url_for("forgot"))
    except Exception:
        pass

    if request.method == "POST":
        pw1 = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if not pw1 or pw1 != pw2:
            flash("Passwords must match.")
            return render_template("reset.html", token=token)

        users = load_users()
        uname = rec["username"]
        for u in users:
            if u["username"] == uname:
                u["password"] = generate_password_hash(pw1)
                break
        save_users(users)

        resets.pop(token, None)
        save_resets(resets)

        flash("Password updated. Please log in.")
        return redirect(url_for("login"))

    return render_template("reset.html", token=token)

# ─────────────────────────────── Home / Dashboard ─────────────────────────────
@app.route("/")
@login_required
def index():
    username = session["username"]
    role = session.get("role","member")
    today = date.today()

    users = load_users()
    uname_to_disp = {u["username"]: u.get("display_name") or u["username"].title() for u in users}

    # tasks
    tasks_all = load_tasks()
    for t in tasks_all:
        d = t.get("due") or t.get("due_date")
        due_d = parse_date(d)
        t["overdue"]   = (due_d is not None) and (not t.get("done", False)) and (due_d < today)
        t["due_today"] = (due_d is not None) and (not t.get("done", False)) and (due_d == today)

    # visible tasks
    visible = tasks_all if role == "manager" else [
        t for t in tasks_all if assigned_to_me(t, username, users)
    ]

    assignees = sorted({t.get("assigned_to") for t in tasks_all if t.get("assigned_to")})

    # group cards (latest + unread)
    groups = load_groups()
    messages_by_group = load_group_messages()
    seen_map_all = load_group_seen()
    user_seen = seen_map_all.get(username, {})

    my_groups = groups if role == "manager" else [g for g in groups if username in g.get("members", [])]
    group_cards = []
    for g in my_groups:
        gid = g["id"]
        msgs = messages_by_group.get(gid, [])
        last_msg = msgs[-1] if msgs else None
        last_seen = user_seen.get(gid, "1970-01-01T00:00")
        unread = sum(1 for m in msgs if m.get("timestamp","") > last_seen)
        sender_display = uname_to_disp.get(last_msg["sender"], last_msg["sender"].title()) if last_msg else None
        group_cards.append({
            "id": gid,
            "name": g["name"],
            "unread": unread,
            "last": last_msg,
            "last_sender_display": sender_display
        })
    def _last_ts(gc): return gc["last"]["timestamp"] if gc["last"] else ""
    group_cards.sort(key=lambda gc: (_last_ts(gc), gc["unread"]), reverse=True)

    # reminders for current user
    all_rem = load_reminders()
    my_rem = [r for r in all_rem if r.get("user") == username]
    now = datetime.now()
    for r in my_rem:
        dt_str = r.get("due_at")
        if dt_str:
            r_dt = parse_dt_any(dt_str)
            if r_dt:
                r["is_due"] = r_dt <= now
                r["nice"] = r_dt.strftime("%b %d, %Y %I:%M %p")
            else:
                r["is_due"] = False
                r["nice"] = dt_str
        else:
            r["is_due"] = False
            r["nice"] = "No time"

    # preferences (theme, density, default calendar scope)
    prefs = load_prefs().get(username, {"theme":"light","density":"comfortable","calendar_scope":"my","notify_sound":False})

    return render_template(
        "index.html",
        tasks=visible,
        assignees=assignees,
        role=role,
        group_cards=group_cards,
        reminders=my_rem,
        prefs=prefs
    )

# ─────────────────────────────── Reminders ───────────────────────────────
@app.route("/reminders/add", methods=["POST"], endpoint="add_reminder")
@login_required
def reminders_add():
    text = request.form.get("text","").strip()
    due  = request.form.get("due_at","").strip()
    if not text:
        flash("Reminder text required.")
        return redirect(url_for("index"))
    items = load_reminders()
    items.append({
        "id": str(uuid.uuid4()),
        "user": session["username"],
        "text": text,
        "due_at": due,
        "done": False
    })
    save_reminders(items)
    flash("Reminder added.")
    return redirect(url_for("index"))

@app.route("/reminders/<rid>/delete", methods=["POST"], endpoint="reminders_delete")
@login_required
def reminders_delete(rid):
    items = load_reminders()
    items = [r for r in items if not (r.get("id")==rid and r.get("user")==session["username"])]
    save_reminders(items)
    flash("Reminder deleted.")
    return redirect(url_for("index"))

@app.route("/reminders/<rid>/toggle", methods=["POST"])
@login_required
def reminders_toggle(rid):
    items = load_reminders()
    for r in items:
        if r.get("id")==rid and r.get("user")==session["username"]:
            r["done"] = not r.get("done", False)
            break
    save_reminders(items)
    return redirect(url_for("index"))

# ─────────────────────────────── Global Search ───────────────────────────────
@app.route("/search")
@login_required
def search():
    q = request.args.get("q","").strip().lower()
    user = session["username"]
    role = session.get("role","member")

    users = load_users()
    uname_to_disp = {u["username"]: u.get("display_name") or u["username"].title() for u in users}

    # tasks (respect visibility)
    ts_all = load_tasks()
    if role != "manager":
        ts_all = [t for t in ts_all if assigned_to_me(t, user, users) or _norm(t.get("created_by")) == _norm(user)]
    task_hits = [
        (i, t) for i, t in enumerate(ts_all)
        if q and (q in (t.get("text","").lower()) or q in (t.get("notes","").lower()) or q in (t.get("assigned_to","").lower()))
    ]

    # groups (respect membership)
    groups = load_groups()
    if role != "manager":
        groups = [g for g in groups if user in g.get("members", [])]
    groups_by_id = {g["id"]: g for g in groups}

    # group messages
    all_msgs = load_group_messages()
    msg_hits = []
    if q:
        for gid, glist in all_msgs.items():
            if gid not in groups_by_id:
                continue
            for m in glist:
                if q in m.get("text","").lower() or q in m.get("sender","").lower():
                    msg_hits.append({
                        "group": groups_by_id[gid],
                        "msg": m,
                        "sender_display": uname_to_disp.get(m["sender"], m["sender"].title())
                    })

    # users
    user_hits = [u for u in users if q and (q in u["username"].lower() or q in (u.get("display_name","").lower()))]

    return render_template("search_results.html",
                           q=q,
                           task_hits=task_hits,
                           msg_hits=msg_hits,
                           user_hits=user_hits)

# ─────────────────────────────── Task CRUD / Manager Pages ───────────────────
@app.route("/add", methods=["POST"])
@login_required
def add():
    text = request.form.get("task","").strip()
    if not text:
        flash("Task text required.")
        return redirect(url_for("tasks_page" if session.get("role")=="manager" else "index"))

    priority = request.form.get("priority","Medium")
    assignee_raw = request.form.get("assigned_to","").strip()
    assignee_key = _norm(assignee_raw)
    due_date = request.form.get("due_date","").strip()
    recurring = "weekly" if request.form.get("recurring")=="weekly" else None
    notes = request.form.get("notes","").strip()
    created_by = session["username"]

    users = load_users()
    assignee_user = next((u for u in users if _norm(u["username"]) == assignee_key), None)
    if assignee_user:
        assigned_display = assignee_user.get("display_name") or assignee_user["username"].title()
        assigned_username = _norm(assignee_user["username"])
    elif assignee_key:
        assigned_display = assignee_raw or assignee_key.title()
        assigned_username = assignee_key
    else:
        assigned_display = ""
        assigned_username = None

    new = {
        "text": text,
        "done": False,
        "priority": priority,
        "assigned_to": assigned_display,      # display label
        "assigned_username": assigned_username,  # canonical login name (may be None if added without assignee)
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
    return redirect(url_for("tasks_page" if session.get("role")=="manager" else "index"))

@app.route("/toggle/<int:task_id>", methods=["POST"])
@login_required
def toggle(task_id):
    username = session["username"]
    role = session.get("role","member")
    users = load_users()
    ts = load_tasks()
    if 0<=task_id<len(ts):
        t = ts[task_id]
        if role=="manager" or assigned_to_me(t, username, users):
            t["done"] = not t.get("done",False)
            if t["done"]:
                t["completed_at"] = datetime.now().isoformat(timespec="minutes")
                if t.get("recurring")=="weekly" and t.get("due_date"):
                    try:
                        dd = parse_date(t.get("due_date"))
                        if dd:
                            nxt = dd + timedelta(weeks=1)
                            new = {**t, "done":False, "due_date":nxt.isoformat()}
                            new.pop("completed_at",None)
                            ts.append(new)
                    except Exception:
                        pass
            else:
                t.pop("completed_at",None)
    save_tasks(ts)
    flash("Task status updated.")
    return redirect(url_for("tasks_page" if role=="manager" else "index"))

@app.route("/remove/<int:task_id>")
@login_required
def remove(task_id):
    username = session["username"]
    role = session.get("role","member")
    users = load_users()
    ts = load_tasks()
    if 0<=task_id<len(ts):
        t = ts[task_id]
        if role=="manager" or assigned_to_me(t, username, users):
            ts.pop(task_id)
            save_tasks(ts)
            flash("Task removed.")
        else:
            flash("Not authorized to remove this task.")
    return redirect(url_for("tasks_page" if role=="manager" else "index"))

@app.route("/edit/<int:task_id>", methods=["GET","POST"])
@login_required
def edit(task_id):
    username = session["username"]
    role = session.get("role","member")
    users = load_users()
    ts = load_tasks()

    if request.method=="POST":
        if 0<=task_id<len(ts):
            t = ts[task_id]
            if role=="manager" or assigned_to_me(t, username, users):
                t["text"] = request.form.get("task","").strip() or t.get("text","")
                t["priority"] = request.form.get("priority","Medium")
                if role=="manager":
                    assignee_raw = request.form.get("assigned_to","").strip()
                    assignee_key = _norm(assignee_raw)
                    assignee_user = next((u for u in users if _norm(u["username"]) == assignee_key), None)
                    if assignee_user:
                        t["assigned_to"] = assignee_user.get("display_name") or assignee_user["username"].title()
                        t["assigned_username"] = _norm(assignee_user["username"])
                    elif assignee_key:
                        # fallback if typed manually
                        t["assigned_to"] = assignee_raw or assignee_key.title()
                        t["assigned_username"] = assignee_key
                    else:
                        t["assigned_to"] = ""
                        t["assigned_username"] = None
                t["due_date"] = request.form.get("due_date","").strip()
                t["notes"] = request.form.get("notes","").strip()
                save_tasks(ts)
                flash("Task updated.")
        return redirect(url_for("tasks_page" if role=="manager" else "index"))
    else:
        if 0<=task_id<len(ts):
            t = ts[task_id]
            if role=="manager" or assigned_to_me(t, username, users):
                assignable = [u["username"] for u in users if u.get("role")!="manager"]
                return render_template("edit.html", task=t, task_id=task_id, assignable_users=assignable)
    return redirect(url_for("tasks_page" if role=="manager" else "index"))

@app.route("/tasks")
@manager_required
def tasks_page():
    # Managers only view (sidebar for managers shows this link)
    sort_by  = request.args.get("sort","due_asc")
    ts = load_tasks()

    if sort_by=="completed":
        ts = [t for t in ts if t.get("done")]

    def keyfn(t):
        dd = t.get("due") or t.get("due_date")
        pd_val = {"High":0,"Medium":1,"Low":2}.get(t.get("priority","Medium"), 1)
        mapping = {
            "due_asc":     parse_date_any(dd, default_far=True),
            "due_desc":   -parse_date_any(dd, default_far=True).timestamp(),
            "priority_hl": pd_val,
            "priority_lh": -pd_val,
            "completed":   parse_date_any(t.get("completed_at"), default_far=True)
        }
        return mapping[sort_by]
    ts.sort(key=keyfn)

    users = load_users()
    elig = {"assistant manager","family swim supervisor","lead supervisor","swim administrator","programming supervisor","supervisor"}
    assignable = [u["username"] for u in users if u["role"]!="manager" and any(t.lower() in elig for t in u.get("titles",[]))]

    return render_template("task_manager.html",
                           tasks=ts,
                           role="manager",
                           sort_by=sort_by,
                           assignable_users=assignable)

@app.route("/tasks/create", methods=["GET","POST"])
@manager_required
def create_task_page():
    if request.method=="POST":
        return redirect(url_for("add"))
    users = load_users()
    elig = {"assistant manager","family swim supervisor","lead supervisor","swim administrator","programming supervisor","supervisor"}
    assignable = [u["username"] for u in users if u["role"]!="manager" and any(t.lower() in elig for t in u.get("titles",[]))]
    return render_template("create_task.html", assignable_users=assignable)

# ─────────────────────────────── Shifts ───────────────────────────────
@app.route("/shifts")
@manager_required
def view_shifts():
    return render_template("shifts.html", shifts=load_shifts())

@app.route("/shifts/add", methods=["GET","POST"])
@manager_required
def add_shift():
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
        flash("All fields except notes are required.")
    users = load_users()
    return render_template("add_shift.html", employees=[u["username"] for u in users if u.get("role")!="manager"])

@app.route("/my-shifts")
@login_required
def my_shifts():
    u = session["username"]
    sh = [s for s in load_shifts() if _norm(s.get("assigned_to"))==_norm(u)]
    return render_template("my_shifts.html", shifts=sh)

# ─────────────────────────────── Team / Titles ───────────────────────────────
@app.route("/members")
@manager_required
def team_member_manager():
    return render_template("team_manager.html", users=load_users())

@app.route("/titles", methods=["GET","POST"])
@manager_required
def title_manager():
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
@manager_required
def update_titles():
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

# ─────────────────────────────── Calendar ───────────────────────────────
@app.route("/calendar")
@login_required
def calendar_view():
    return render_template("calendar.html", role=session.get("role","member"))

# New unified feed (tasks + shifts), supports ?scope=my|all
@app.route("/api/calendar")
@login_required
def calendar_feed():
    scope = request.args.get("scope","my")  # "my" or "all"
    user  = session["username"]
    role  = session.get("role","member")
    show_all = (scope == "all" and role == "manager")

    users = load_users()

    # tasks
    t_all = load_tasks()
    tasks = t_all if show_all else [
        t for t in t_all if assigned_to_me(t, user, users) or _norm(t.get("created_by")) == _norm(user)
    ]

    events = []
    for i, t in enumerate(tasks):
        start = t.get("due") or t.get("due_date")
        if not start:
            continue
        pr = t.get("priority", "Medium")
        color_map = {"High":"#E6492D", "Medium":"#F3B43E", "Low":"#2FA77A"}
        events.append({
            "id": f"task-{i}",
            "title": f"{t.get('text','Task')} ({pr})",
            "start": start,
            "allDay": True,
            "color": color_map.get(pr, "#2FA77A"),
            "extendedProps": {"type":"task"}
        })

    # shifts
    sh_all = load_shifts()
    shifts = sh_all if show_all else [s for s in sh_all if _norm(s.get("assigned_to"))==_norm(user)]
    for j, s in enumerate(shifts):
        start = s.get("date")
        if not start:
            continue
        events.append({
            "id": f"shift-{j}",
            "title": f"Shift {s.get('start_time','')}–{s.get('end_time','')}",
            "start": start,
            "allDay": True,
            "color": "#4C6EF5",
            "extendedProps": {"type":"shift"}
        })

    return jsonify(events)

# Legacy (tasks only) – kept for backward compatibility
@app.route("/api/tasks/events")
@login_required
def task_events():
    evts=[]
    for i,t in enumerate(load_tasks()):
        start_date = t.get("due") or t.get("due_date")
        if start_date:
            pr = t.get("priority","Medium")
            color_map = {"High":"#f66","Medium":"#fa6","Low":"#6a6"}
            evts.append({
                "id": i,
                "title": f"{t.get('text','Task')} ({pr})",
                "start": start_date,
                "color": color_map.get(pr, "#6a6"),
                "extendedProps": {
                    "assigned_to":t.get("assigned_to",""),
                    "notes":t.get("notes",""),
                    "done":t.get("done",False)
                }
            })
    return jsonify(evts)

# ─────────────────────────────── Settings ───────────────────────────────
@app.route("/settings", methods=["GET"])
@login_required
def settings():
    user = session["username"]

    # load prefs (with defaults)
    prefs_all = load_prefs()
    prefs = prefs_all.get(user, {
        "theme": "light",
        "density": "comfortable",
        "calendar_scope": "my",
        "notify_sound": False
    })

    # load the user's current display name
    users = load_users()
    urec = next((u for u in users if u["username"] == user), None)
    effective_display = (urec.get("display_name") if urec and urec.get("display_name")
                         else user.title())

    # build my_prefs for the template (what your settings.html expects)
    my_prefs = dict(prefs)
    my_prefs.setdefault("display_name", effective_display)

    return render_template(
        "settings.html",
        prefs=prefs,                    # backward compatibility
        my_prefs=my_prefs,              # what the template uses: my_prefs.*
        effective_display=effective_display
    )

@app.route("/settings/update", methods=["POST"])
@login_required
def settings_update():
    user = session["username"]

    # 1) Save preferences
    prefs_all = load_prefs()
    prefs = prefs_all.get(user, {})
    prefs["theme"] = request.form.get("theme", "light")
    prefs["density"] = request.form.get("density", "comfortable")
    prefs["calendar_scope"] = request.form.get("calendar_scope", "my")
    prefs["notify_sound"] = bool(request.form.get("notify_sound"))
    prefs_all[user] = prefs
    save_prefs(prefs_all)

    # 2) Optionally update user's display_name
    new_display = request.form.get("display_name", "").strip()
    if new_display:
        users = load_users()
        for u in users:
            if u["username"] == user:
                u["display_name"] = new_display
                break
        save_users(users)

    flash("Settings saved.")
    return redirect(url_for("settings"))

# ─────────────────────────────── Overdue ───────────────────────────────
@app.route("/overdue")
@login_required
def overdue_tasks():
    username = session["username"]
    role = session.get("role", "member")
    today = date.today()

    users = load_users()
    tasks = load_tasks()

    overdue_entries = []
    for idx, task in enumerate(tasks):
        if task.get("done"):
            continue

        if role != "manager" and not assigned_to_me(task, username, users):
            continue

        due_raw = task.get("due") or task.get("due_date") or ""
        due_dt = parse_dt_any(due_raw)
        if not due_dt:
            continue

        if due_dt.date() >= today:
            continue

        overdue_entries.append({
            "idx": idx,
            "task": task,
            "due_dt": due_dt,
            "due_display": due_dt.strftime("%b %d, %Y"),
        })

    overdue_entries.sort(key=lambda item: item["due_dt"])

    return render_template("overdue.html", overdue=overdue_entries)

# ─────────────────────────────── Group Chats ───────────────────────────────
@app.route("/groups")
@manager_required
def group_chat_manager():
    groups = load_groups()
    users  = load_users()
    supervisors = [u for u in users if any(t.lower()=="supervisor" for t in u.get("titles",[]))]
    return render_template("group_chat_manager.html",
                           groups=groups,
                           supervisors=supervisors,
                           users=users)

# Member-facing chats hub (non-managers list only their groups)
@app.route("/chats")
@login_required
def chats():
    user = session["username"]
    role = session.get("role", "member")
    if role == "manager":
        # Managers manage chats in the admin view
        return redirect(url_for("group_chat_manager"))

    groups = load_groups()
    msgs_by_group = load_group_messages()
    seen_map = load_group_seen().get(user, {})

    my_groups = [g for g in groups if user in g.get("members", [])]
    cards = []
    for g in my_groups:
        gid = g["id"]
        lst = msgs_by_group.get(gid, [])
        last_msg = lst[-1] if lst else None
        last_seen = seen_map.get(gid, "1970-01-01T00:00")
        unread = sum(1 for m in lst if m.get("timestamp","") > last_seen)
        cards.append({
            "id": gid,
            "name": g.get("name", "Group"),
            "unread": unread,
            "last": last_msg
        })

    def _ts(c): return c["last"]["timestamp"] if c["last"] else ""
    cards.sort(key=lambda c: (_ts(c), c["unread"]), reverse=True)

    return render_template("my_chats.html", cards=cards)

@app.route("/groups/<group_id>")
@login_required
def view_group(group_id):
    user = session["username"]
    role = session.get("role","member")
    gs   = load_groups()
    grp  = next((g for g in gs if g["id"]==group_id), None)
    if not grp or (role!="manager" and user not in grp.get("members",[])):
        # Non-members/managers: bounce to the appropriate hub
        return redirect(url_for("group_chat_manager" if role=="manager" else "chats"))

    tasks    = load_group_tasks().get(group_id,[])
    messages = load_group_messages().get(group_id,[])
    users    = load_users()

    # mark read
    seen_map = load_group_seen()
    user_seen = seen_map.setdefault(user, {})
    if messages:
        user_seen[group_id] = messages[-1]["timestamp"]
        save_group_seen(seen_map)

    return render_template("group_chat.html",
                           group=grp,
                           users=users,
                           tasks=tasks,
                           messages=messages)

@app.route("/groups/mark_all_read", methods=["POST"])
@login_required
def groups_mark_all_read():
    user = session["username"]
    role = session.get("role","member")
    groups = load_groups()
    msgs_by_group = load_group_messages()

    allowed_ids = [g["id"] for g in groups] if role=="manager" else [
        g["id"] for g in groups if user in g.get("members", [])
    ]

    seen_map = load_group_seen()
    user_seen = seen_map.setdefault(user, {})
    for gid in allowed_ids:
        lst = msgs_by_group.get(gid, [])
        if lst:
            user_seen[gid] = lst[-1]["timestamp"]
    save_group_seen(seen_map)
    return redirect(url_for("index"))

@app.route("/groups/<group_id>/message", methods=["POST"])
@login_required
def post_group_message(group_id):
    text = request.form.get("message","").strip()
    img  = request.files.get("image")

    # membership (managers can always post)
    user = session["username"]
    role = session.get("role","member")
    group = next((g for g in load_groups() if g["id"] == group_id), None)
    if not group or (role != "manager" and user not in group.get("members", [])):
        flash("Not authorized.")
        return redirect(url_for("group_chat_manager"))

    if not text and not (img and img.filename):
        flash("Type a message or attach an image.")
        return redirect(url_for("view_group", group_id=group_id))

    image_filename = None
    if img and img.filename and allowed_file(img.filename):
        safe = secure_filename(f"{uuid.uuid4()}_{img.filename}")
        img.save(os.path.join(app.config["UPLOAD_FOLDER"], safe))
        image_filename = safe

    msgs = load_group_messages()
    channel = msgs.setdefault(group_id, [])
    channel.append({
        "sender": user,
        "timestamp": datetime.now().isoformat(timespec="minutes"),
        "text": text,
        "image": image_filename,
        "pinned": False
    })
    save_group_messages(msgs)

    # mark sender as read
    seen_map = load_group_seen()
    seen_map.setdefault(user, {})[group_id] = channel[-1]["timestamp"]
    save_group_seen(seen_map)

    return redirect(url_for("view_group", group_id=group_id))

@app.route("/groups/<group_id>/pin/<int:idx>", methods=["POST"])
@login_required
def pin_message(group_id, idx):
    user = session["username"]
    role = session.get("role","member")
    groups = load_groups()
    grp = next((g for g in groups if g["id"]==group_id), None)
    if not grp:
        abort(404)
    if not (role=="manager" or user==grp.get("supervisor")):
        flash("Not authorized to pin.")
        return redirect(url_for("view_group", group_id=group_id))

    msgs = load_group_messages()
    lst = msgs.get(group_id, [])
    if 0 <= idx < len(lst):
        lst[idx]["pinned"] = not lst[idx].get("pinned", False)
        save_group_messages(msgs)
        flash("Pinned" if lst[idx]["pinned"] else "Unpinned")
    return redirect(url_for("view_group", group_id=group_id))

@app.route("/groups/<group_id>/delete/<int:idx>", methods=["POST"])
@login_required
def delete_message(group_id, idx):
    user = session["username"]
    role = session.get("role","member")

    groups = load_groups()
    grp = next((g for g in groups if g["id"] == group_id), None)
    if not grp:
        abort(404)

    msgs = load_group_messages()
    lst = msgs.get(group_id, [])
    if 0 <= idx < len(lst):
        can_delete = (role == "manager") or (user == lst[idx]["sender"])
        if not can_delete:
            flash("You can only delete your messages.")
            return redirect(url_for("view_group", group_id=group_id))
        lst.pop(idx)
        save_group_messages(msgs)
        flash("Message deleted.")
    return redirect(url_for("view_group", group_id=group_id))

@app.route("/groups/<group_id>/add_member", methods=["POST"])
@manager_required
def add_group_member(group_id):
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
@manager_required
def remove_group_member(group_id):
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

    # members or managers only
    user = session["username"]
    role = session.get("role","member")
    grp  = next((g for g in load_groups() if g["id"]==group_id), None)
    if not grp or (role!="manager" and user not in grp.get("members",[])):
        flash("Not authorized.")
        return redirect(url_for("group_chat_manager"))

    gts = load_group_tasks()
    ch  = gts.setdefault(group_id,[])
    ch.append({
        "text": text,
        "priority": priority,
        "recurring": "weekly",
        "notes": notes,
        "done": False,
        "created_by": user,
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
    if not grp or user not in grp.get("members",[]):
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

# ─────────────────────────────── Run ───────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)
