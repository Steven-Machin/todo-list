# app.py
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify, abort
)
import calendar
import json, os, uuid, secrets, contextlib, tempfile
from datetime import datetime, timedelta, date
from functools import wraps, lru_cache
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    current_user,
    login_required,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from notifications.config import (
    DEFAULT_NOTIFICATION_PREFS as NOTIFICATION_DEFAULTS,
    VALID_CHANNELS as NOTIFICATION_CHANNELS,
    VALID_FREQUENCIES as NOTIFICATION_FREQUENCIES,
    WEEKDAY_OPTIONS as NOTIFICATION_WEEKDAYS,
)


try:
    import fcntl  # type: ignore[import]
except ImportError:
    fcntl = None

try:
    import portalocker  # type: ignore[import]
except ImportError:
    portalocker = None

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-key")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in first."

DEBUG = os.getenv("FLASK_ENV") != "production"

APP_MODE = os.environ.get("APP_MODE", "prod").lower()
DEMO = APP_MODE == "demo"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)

os.makedirs(DATA_DIR, exist_ok=True)


def data_path(name: str) -> str:
    return os.path.join(DATA_DIR, name)

# ------------------------------- Paths / Config -------------------------------
TASKS_FILE            = data_path("tasks.json")
USERS_FILE            = data_path("users.json")
SHIFTS_FILE           = data_path("shifts.json")
TITLES_FILE           = data_path("titles.json")

GROUPS_FILE           = data_path("group_chats.json")
GROUP_TASKS_FILE      = data_path("group_tasks.json")
GROUP_MESSAGES_FILE   = data_path("group_messages.json")
GROUP_SEEN_FILE       = data_path("group_seen.json")
BADGES_FILE           = data_path("badges.json")
USER_BADGES_FILE      = data_path("user_badges.json")

REMINDERS_FILE        = data_path("reminders.json")
PREFERENCES_FILE      = data_path("preferences.json")
PASSWORD_RESETS_FILE  = data_path("password_resets.json")   # forgot/reset flow

UPLOAD_FOLDER         = "static/uploads"
ALLOWED_EXTS          = {"png","jpg","jpeg","gif"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

USE_DATABASE = os.getenv("USE_DATABASE", "1").strip().lower() not in {"0", "false", "no"}
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")
DEFAULT_SQLITE_URL = f"sqlite:///{data_path('todo.db')}"

if not DATABASE_URL and USE_DATABASE:
    DATABASE_URL = DEFAULT_SQLITE_URL

DB_ENABLED = bool(USE_DATABASE and DATABASE_URL)

SessionLocal: Optional[sessionmaker] | None = None
UserModel = None
TaskModel = None
Base = None

if DB_ENABLED:
    engine_kwargs: dict[str, Any] = {"future": True}
    if str(DATABASE_URL).startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
        pool_pre_ping = False
    else:
        pool_pre_ping = True
    engine = create_engine(DATABASE_URL, pool_pre_ping=pool_pre_ping, **engine_kwargs)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    Base = declarative_base()

    class UserModel(Base):
        __tablename__ = "users"
        username = Column(String(80), primary_key=True)
        display_name = Column(String(120))
        password_hash = Column(String(255))
        role = Column(String(20), default="member", nullable=False)
        titles = Column(JSON, default=list)
        join_date = Column(DateTime, default=datetime.utcnow, nullable=False)
        total_tasks_completed = Column(Integer, default=0, nullable=False)
        streak_count = Column(Integer, default=0, nullable=False)
        extra = Column(JSON)

        tasks_created = relationship("TaskModel", back_populates="owner", foreign_keys="TaskModel.owner_username")
        tasks_assigned = relationship("TaskModel", back_populates="assignee", foreign_keys="TaskModel.assigned_username")
        badge_links = relationship("UserBadgeModel", back_populates="user", cascade="all, delete-orphan")

    class TaskModel(Base):
        __tablename__ = "tasks"
        id = Column(Integer, primary_key=True)
        text = Column(String(255), nullable=False)
        done = Column(Boolean, default=False, nullable=False)
        priority = Column(String(20), default="Medium")
        notes = Column(Text)
        due_date = Column(Date)
        recurring = Column(String(32))
        created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
        completed_at = Column(DateTime)
        overdue = Column(Boolean, default=False, nullable=False)
        assigned_username = Column(String(80), ForeignKey("users.username", ondelete="SET NULL"))
        assigned_display = Column(String(120))
        owner_username = Column(String(80), ForeignKey("users.username", ondelete="SET NULL"))
        completed_by_username = Column(String(80), ForeignKey("users.username", ondelete="SET NULL"))
        position = Column(Integer, default=0, nullable=False)
        extra = Column(JSON)

        assignee = relationship("UserModel", foreign_keys=[assigned_username], back_populates="tasks_assigned")
        owner = relationship("UserModel", foreign_keys=[owner_username], back_populates="tasks_created")
        completed_by = relationship("UserModel", foreign_keys=[completed_by_username])

    class BadgeModel(Base):
        __tablename__ = "badges"
        id = Column(Integer, primary_key=True)
        slug = Column(String(64), unique=True, nullable=False)
        name = Column(String(120), nullable=False)
        description = Column(Text)
        icon_path = Column(String(255))

        users = relationship("UserBadgeModel", back_populates="badge", cascade="all, delete-orphan")

    class UserBadgeModel(Base):
        __tablename__ = "user_badges"
        __table_args__ = (UniqueConstraint("username", "badge_id", name="uq_user_badge"),)
        id = Column(Integer, primary_key=True)
        username = Column(String(80), ForeignKey("users.username", ondelete="CASCADE"), nullable=False)
        badge_id = Column(Integer, ForeignKey("badges.id", ondelete="CASCADE"), nullable=False)
        earned_at = Column(DateTime, default=datetime.utcnow, nullable=False)

        user = relationship("UserModel", back_populates="badge_links")
        badge = relationship("BadgeModel", back_populates="users")

    Base.metadata.create_all(bind=engine)
else:
    Base = None
    SessionLocal = None

NAV_ITEMS = [
    {"label": "Home", "endpoint": "dashboard", "roles": ["member", "manager"], "icon": None},
    {"label": "Calendar", "endpoint": "calendar", "roles": ["member", "manager"], "icon": None},
    {"label": "Overdue", "endpoint": "overdue", "roles": ["member", "manager"], "icon": None},
    {"label": "My Chats", "endpoint": "my_chats", "roles": ["member", "manager"], "icon": None},
    {"label": "My Shifts", "endpoint": "my_shifts", "roles": ["member", "manager"], "icon": None},
    {"label": "Settings", "endpoint": "settings", "roles": ["member", "manager"], "icon": None},
    {"label": "My Badges", "endpoint": "my_badges", "roles": ["member", "manager"], "icon": None},

    {"label": "Task Manager", "endpoint": "task_manager", "roles": ["manager"], "icon": None},
    {"label": "Team Member Manager", "endpoint": "team_manager", "roles": ["manager"], "icon": None},
    {"label": "Group Chat Manager", "endpoint": "group_chat_manager", "roles": ["manager"], "icon": None},
    {"label": "Title Manager", "endpoint": "title_manager", "roles": ["manager"], "icon": None},
    {"label": "Shifts", "endpoint": "shifts", "roles": ["manager"], "icon": None},
]

BADGE_SLUG_FIRST_STEP = "first_step"
BADGE_SLUG_TASK_MASTER = "task_master"
BADGE_SLUG_WEEKLY_WARRIOR = "weekly_warrior"

DEFAULT_BADGES = [
    {
        "slug": BADGE_SLUG_FIRST_STEP,
        "name": "First Step",
        "description": "Complete your first task.",
        "icon_path": "/static/badges/first_step.svg",
    },
    {
        "slug": BADGE_SLUG_TASK_MASTER,
        "name": "Task Master",
        "description": "Complete 100 tasks.",
        "icon_path": "/static/badges/task_master.svg",
    },
    {
        "slug": BADGE_SLUG_WEEKLY_WARRIOR,
        "name": "Weekly Warrior",
        "description": "Complete tasks every day for seven days in a row.",
        "icon_path": "/static/badges/weekly_warrior.svg",
    },
]

DEFAULT_BADGES_BY_SLUG = {badge["slug"]: badge for badge in DEFAULT_BADGES}




BADGE_PROGRESS_ORDER = [
    BADGE_SLUG_FIRST_STEP,
    BADGE_SLUG_TASK_MASTER,
    BADGE_SLUG_WEEKLY_WARRIOR,
]

BADGE_PROGRESS_RULES: Dict[str, Dict[str, Any]] = {
    BADGE_SLUG_FIRST_STEP: {"metric": "completed_count", "target": 1, "label": "Complete 1 task"},
    BADGE_SLUG_TASK_MASTER: {"metric": "completed_count", "target": 100, "label": "Complete 100 tasks"},
    BADGE_SLUG_WEEKLY_WARRIOR: {"metric": "longest_streak", "target": 7, "label": "Achieve a 7-day streak"},
}



# ------------------------------- Utilities -------------------------------
# ------------------------------- Auth model -------------------------------
class AppUser(UserMixin):
    def __init__(self, username: str, role: str = "member", display_name: str | None = None):
        self.id = username
        self.username = username
        self.role = (role or "member").lower()
        self.display_name = display_name or username.title()

    @property
    def is_manager(self) -> bool:
        return self.role == "manager"

    @classmethod
    def from_record(cls, record: dict):
        username = record.get("username")
        if not username:
            raise ValueError("User record missing username")
        return cls(
            username=username,
            role=record.get("role", "member"),
            display_name=record.get("display_name"),
        )




def current_username() -> str | None:
    if current_user.is_authenticated:
        return current_user.username
    return None


def current_role() -> str:
    if current_user.is_authenticated:
        return getattr(current_user, "role", "member")
    return "member"


def require_username() -> str:
    username = current_username()
    if not username:
        abort(401)
    return username

@contextlib.contextmanager
def with_json_lock(path: str):
    lock_path = f"{path}.lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    lock_file = open(lock_path, "a+")
    locked_via = None
    try:
        if "portalocker" in globals() and portalocker is not None:
            portalocker.lock(lock_file, portalocker.LOCK_EX)
            locked_via = "portalocker"
        elif "fcntl" in globals() and fcntl is not None:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            locked_via = "fcntl"
        yield
    finally:
        try:
            if locked_via == "portalocker" and portalocker is not None:
                portalocker.unlock(lock_file)
            elif locked_via == "fcntl" and fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
        finally:
            lock_file.close()


def save_json_atomic(path, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with with_json_lock(path):
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


def load_json(path, default):
    try:
        with with_json_lock(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except FileNotFoundError:
        save_json_atomic(path, default)
        return default
    except json.JSONDecodeError:
        return default
    except OSError:
        return default


def save_json(path, data):
    save_json_atomic(path, data)


def allowed_file(fn):
    return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_EXTS

def manager_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if getattr(current_user, "role", "member") != "manager":
            flash("Managers only.")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper

def demo_guard(fn):
    @wraps(fn)
    def _w(*args, **kwargs):
        if DEMO and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            return ("Demo mode: mutations are disabled.", 403)
        return fn(*args, **kwargs)
    return _w

def visible_nav(role: str | None):
    role = (role or "member").lower()
    return [item for item in NAV_ITEMS if role in item["roles"]]


def is_active(endpoint: str):
    return getattr(request, 'endpoint', None) == endpoint


@app.context_processor
def inject_flags():
    return {"DEMO": DEMO, "APP_MODE": APP_MODE}


def resolve_theme_preference(username: str | None) -> str:
    theme = session.get("theme")
    if theme in {"light", "dark"}:
        return theme
    if username:
        prefs_all = load_prefs()
        if isinstance(prefs_all, dict):
            record = prefs_all.get(username, {}) or {}
            theme = record.get("theme")
            if theme in {"light", "dark"}:
                session["theme"] = theme
                return theme
    return "light"


@app.context_processor
def inject_user_ctx():
    role = current_role()
    username = current_username()
    display = getattr(current_user, "display_name", None) if current_user.is_authenticated else None
    theme = resolve_theme_preference(username)
    return {
        "current_username": username,
        "current_user_display": display,
        "current_role": role,
        "nav_items": visible_nav(role),
        "current_theme": theme,
        "is_active": is_active,
    }


def _norm(s):
    return (s or "").strip().lower()

def normalize_tags(value: Any) -> list[str]:
    """Return a cleaned list of tag strings from form or stored data."""
    if not value:
        return []
    if isinstance(value, list):
        cleaned: list[str] = []
        for item in value:
            if not item:
                continue
            token = str(item).strip()
            if token:
                cleaned.append(token)
        return cleaned
    if isinstance(value, str):
        raw = value.replace(';', ",")
        return [token.strip() for token in raw.split(",") if token.strip()]
    token = str(value).strip()
    return [token] if token else []

VALID_RECURRENCE = {"daily", "weekly", "monthly"}

def normalize_recurring(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text_val = value.strip().lower()
    else:
        text_val = str(value).strip().lower()
    if not text_val or text_val in {"none", "no", "false"}:
        return None
    if text_val in VALID_RECURRENCE:
        return text_val
    if text_val in {"yes", "true"}:
        return "weekly"
    return None

def next_recurring_due_date(current: date, pattern: str) -> Optional[date]:
    freq = normalize_recurring(pattern)
    if not freq:
        return None
    if freq == "daily":
        return current + timedelta(days=1)
    if freq == "weekly":
        return current + timedelta(weeks=1)
    if freq == "monthly":
        month = current.month + 1
        year = current.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, min(current.day, last_day))
    return None

def apply_task_defaults(task: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure optional fields exist on task dictionaries."""
    task.setdefault("tags", [])
    task["tags"] = normalize_tags(task.get("tags"))
    task["recurring"] = normalize_recurring(task.get("recurring"))
    return task



# ------------------------------- Badge persistence -------------------------------


def load_badges_json() -> List[Dict[str, Any]]:
    data = load_json(BADGES_FILE, [dict(item) for item in DEFAULT_BADGES])
    return [dict(item) for item in data]


def save_badges_json(data: List[Dict[str, Any]]) -> None:
    save_json(BADGES_FILE, data)


def load_user_badges_json() -> List[Dict[str, Any]]:
    return load_json(USER_BADGES_FILE, [])


def save_user_badges_json(data: List[Dict[str, Any]]) -> None:
    save_json(USER_BADGES_FILE, data)


def _badge_to_dict(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        result = dict(payload)
        result.setdefault("icon_path", result.get("icon_path") or "")
        return result
    return {
        "id": getattr(payload, "id", None),
        "slug": getattr(payload, "slug", None),
        "name": getattr(payload, "name", None),
        "description": getattr(payload, "description", None),
        "icon_path": getattr(payload, "icon_path", "") or "",
    }


@lru_cache(maxsize=1)
def bootstrap_badges() -> bool:
    ensure_default_badges()
    return True


def ensure_default_badges() -> None:
    if DB_ENABLED and SessionLocal is not None:
        with SessionLocal.begin() as session:
            existing = {slug for (slug,) in session.query(BadgeModel.slug).all()}
            for badge in DEFAULT_BADGES:
                if badge["slug"] not in existing:
                    session.add(BadgeModel(
                        slug=badge["slug"],
                        name=badge["name"],
                        description=badge["description"],
                        icon_path=badge.get("icon_path"),
                    ))
    else:
        badges = load_badges_json()
        existing = {_norm(item.get("slug")) for item in badges}
        updated = False
        for badge in DEFAULT_BADGES:
            if _norm(badge["slug"]) not in existing:
                badges.append(dict(badge))
                updated = True
        if updated:
            save_badges_json(badges)


def get_all_badges() -> List[Dict[str, Any]]:
    bootstrap_badges()
    if DB_ENABLED and SessionLocal is not None:
        with SessionLocal() as session:
            rows = session.query(BadgeModel).order_by(BadgeModel.id).all()
            return [_badge_to_dict(row) for row in rows]
    return [dict(item) for item in load_badges_json()]


def get_badge_catalog() -> Dict[str, Dict[str, Any]]:
    return {badge["slug"]: badge for badge in get_all_badges()}


def get_user_badges(username: str) -> List[Dict[str, Any]]:
    bootstrap_badges()
    uname = _norm(username)
    if not uname:
        return []
    if DB_ENABLED and SessionLocal is not None:
        with SessionLocal() as session:
            rows = (
                session.query(UserBadgeModel, BadgeModel)
                .join(BadgeModel, UserBadgeModel.badge_id == BadgeModel.id)
                .filter(UserBadgeModel.username == uname)
                .order_by(UserBadgeModel.earned_at)
                .all()
            )
            results: List[Dict[str, Any]] = []
            for link, badge in rows:
                badge_dict = _badge_to_dict(badge)
                earned_at = iso_minutes(link.earned_at) if getattr(link, "earned_at", None) else None
                badge_dict["earned_at"] = earned_at
                results.append(badge_dict)
            return results
    badge_catalog = get_badge_catalog()
    records = load_user_badges_json()
    results = []
    for entry in records:
        if _norm(entry.get("username")) != uname:
            continue
        badge = badge_catalog.get(entry.get("badge_slug"))
        if not badge:
            continue
        badge_dict = dict(badge)
        badge_dict["earned_at"] = entry.get("earned_at")
        results.append(badge_dict)
    results.sort(key=lambda item: item.get("earned_at") or "")
    return results


def user_has_badge(username: str, badge_slug: str) -> bool:
    return any(badge_slug == badge.get("slug") for badge in get_user_badges(username))


def store_user_badge(username: str, badge_slug: str) -> Optional[Dict[str, Any]]:
    bootstrap_badges()
    uname = _norm(username)
    if not uname:
        return None
    if DB_ENABLED and SessionLocal is not None:
        with SessionLocal.begin() as session:
            badge = session.query(BadgeModel).filter(BadgeModel.slug == badge_slug).one_or_none()
            if not badge:
                return None
            existing = (
                session.query(UserBadgeModel)
                .filter(UserBadgeModel.username == uname, UserBadgeModel.badge_id == badge.id)
                .one_or_none()
            )
            if existing is not None:
                return None
            link = UserBadgeModel(username=uname, badge_id=badge.id, earned_at=datetime.utcnow())
            session.add(link)
            session.flush()
            badge_dict = _badge_to_dict(badge)
            badge_dict["earned_at"] = iso_minutes(link.earned_at) if link.earned_at else None
            return badge_dict
    badge_catalog = get_badge_catalog()
    badge = badge_catalog.get(badge_slug)
    if not badge:
        return None
    records = load_user_badges_json()
    for entry in records:
        if _norm(entry.get("username")) == uname and entry.get("badge_slug") == badge_slug:
            return None
    earned_at = iso_minutes(datetime.utcnow())
    records.append({
        "username": uname,
        "badge_slug": badge_slug,
        "earned_at": earned_at,
    })
    save_user_badges_json(records)
    badge_dict = dict(badge)
    badge_dict["earned_at"] = earned_at
    return badge_dict


def get_user_completion_stats(username: str, tasks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    uname = _norm(username)
    if not uname:
        return {
            "completed_tasks": [],
            "completed_count": 0,
            "completion_dates": [],
            "unique_dates": [],
            "longest_streak": 0,
            "current_streak": 0,
        }
    task_list = tasks if tasks is not None else load_tasks()
    completed_tasks: List[Dict[str, Any]] = []
    for task in task_list:
        if not isinstance(task, dict) or not task.get("done"):
            continue
        completed_by = _norm(task.get("completed_by"))
        if not completed_by and task.get("assigned_username"):
            completed_by = _norm(task.get("assigned_username"))
        if completed_by != uname:
            continue
        completed_tasks.append(task)

    completion_dates: List[date] = []
    for task in completed_tasks:
        stamp = task.get("completed_at")
        dt = parse_dt_any(stamp) if stamp else None
        if dt:
            completion_dates.append(dt.date())

    unique_dates = sorted(set(completion_dates))
    longest_streak = 0
    streak = 0
    prev_date: Optional[date] = None
    for current_date in unique_dates:
        if prev_date and (current_date - prev_date).days == 1:
            streak += 1
        else:
            streak = 1
        if streak > longest_streak:
            longest_streak = streak
        prev_date = current_date

    current_streak = 0
    if unique_dates:
        completion_set = set(unique_dates)
        cursor = date.today()
        while cursor in completion_set:
            current_streak += 1
            cursor -= timedelta(days=1)

    return {
        "completed_tasks": completed_tasks,
        "completed_count": len(completed_tasks),
        "completion_dates": completion_dates,
        "unique_dates": unique_dates,
        "longest_streak": longest_streak,
        "current_streak": current_streak,
    }



def get_next_badge_progress(stats: Dict[str, Any], earned_slugs: set[str], badge_catalog: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for slug in BADGE_PROGRESS_ORDER:
        if slug in earned_slugs:
            continue
        badge = badge_catalog.get(slug)
        rule = BADGE_PROGRESS_RULES.get(slug)
        if not badge or not rule:
            continue
        metric_value = int(stats.get(rule["metric"], 0) or 0)
        target = int(rule.get("target", 0) or 0)
        if target <= 0:
            percent = 100
            remaining = 0
        else:
            percent = min(100, int(round((metric_value / target) * 100)))
            remaining = max(target - metric_value, 0)
        return {
            "slug": slug,
            "badge": dict(badge),
            "current": metric_value,
            "target": target,
            "percent": percent,
            "remaining": remaining,
            "label": rule.get("label"),
            "metric": rule.get("metric"),
        }
    return None



def update_user_progress_fields(username: str, total_completed: int, streak: int) -> None:
    uname = _norm(username)
    if not uname:
        return
    if DB_ENABLED and SessionLocal is not None:
        with SessionLocal.begin() as session:
            record = session.get(UserModel, uname)
            if record:
                record.total_tasks_completed = total_completed
                record.streak_count = streak
                if not record.join_date:
                    record.join_date = datetime.utcnow()
    else:
        users = load_users()
        changed = False
        for user in users:
            if _norm(user.get("username")) != uname:
                continue
            if user.get("total_tasks_completed") != total_completed:
                user["total_tasks_completed"] = total_completed
                changed = True
            if user.get("streak_count") != streak:
                user["streak_count"] = streak
                changed = True
            if not user.get("join_date"):
                user["join_date"] = datetime.utcnow().isoformat(timespec="seconds")
                changed = True
            break
        if changed:
            save_users(users)



def award_badges_for_user(username: str, tasks: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    bootstrap_badges()
    uname = _norm(username)
    if not uname:
        return []
    task_list = tasks if tasks is not None else load_tasks()
    stats = get_user_completion_stats(uname, task_list)
    completed_count = stats.get("completed_count", 0)
    longest_streak = stats.get("longest_streak", 0)

    earned_slugs = {badge.get("slug") for badge in get_user_badges(uname)}
    targets: List[str] = []
    if BADGE_SLUG_FIRST_STEP not in earned_slugs and completed_count >= 1:
        targets.append(BADGE_SLUG_FIRST_STEP)
    if BADGE_SLUG_TASK_MASTER not in earned_slugs and completed_count >= 100:
        targets.append(BADGE_SLUG_TASK_MASTER)
    if BADGE_SLUG_WEEKLY_WARRIOR not in earned_slugs and longest_streak >= 7:
        targets.append(BADGE_SLUG_WEEKLY_WARRIOR)

    newly_awarded = []
    for slug in targets:
        badge_dict = store_user_badge(uname, slug)
        if badge_dict:
            newly_awarded.append(badge_dict)
    return newly_awarded




def csrf_token():
    return session.get("_csrf", "")


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def ensure_csrf_token():
    session["_csrf"] = session.get("_csrf") or secrets.token_urlsafe(32)
    if request.method == "POST":
        token = session.get("_csrf")
        submitted = request.form.get("csrf_token")
        if not token or not submitted or not secrets.compare_digest(submitted, token):
            abort(400)

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


def task_owner(task) -> str:
    return _norm(task.get("owner") or task.get("created_by"))


def task_visible_to(task, username, users=None):
    if users is None:
        users = load_users()
    uname = _norm(username)
    if not uname:
        return False
    return task_owner(task) == uname or assigned_to_me(task, username, users)

# ------------------------------- Persistence helpers -------------------------------
TASK_PERSISTED_KEYS = {
    "text",
    "done",
    "priority",
    "assigned_to",
    "assigned_username",
    "notes",
    "owner",
    "created_by",
    "created_at",
    "due_date",
    "recurring",
    "overdue",
    "completed_at",
    "completed_by",
}

USER_PERSISTED_KEYS = {"username", "display_name", "password", "role", "titles", "join_date", "total_tasks_completed", "streak_count"}


def _optional_username(value: Optional[str]) -> Optional[str]:
    normed = _norm(value)
    return normed or None


def _user_to_dict(model: "UserModel") -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "username": model.username,
        "display_name": model.display_name,
        "password": model.password_hash,
        "role": (model.role or "member"),
        "titles": list(model.titles or []),
    }
    if model.join_date:
        data["join_date"] = iso_minutes(model.join_date)
    data["total_tasks_completed"] = int(model.total_tasks_completed or 0)
    data["streak_count"] = int(model.streak_count or 0)
    if model.extra:
        for key, value in model.extra.items():
            data.setdefault(key, value)
    return data


def _task_to_dict(model: "TaskModel") -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if model.extra:
        data.update(model.extra)

    data["text"] = model.text
    data["done"] = bool(model.done)
    data["priority"] = model.priority or "Medium"
    data["notes"] = model.notes or ""

    assigned_display = model.assigned_display
    if not assigned_display and model.assignee:
        assigned_display = model.assignee.display_name or model.assignee.username.title()
    data["assigned_to"] = assigned_display or ""
    data["assigned_username"] = model.assigned_username

    owner_username = model.owner_username or ""
    data["owner"] = owner_username
    data["created_by"] = owner_username

    data["overdue"] = bool(model.overdue)

    if model.due_date:
        data["due_date"] = model.due_date.isoformat()
    if model.recurring is not None:
        data["recurring"] = model.recurring
    else:
        data.setdefault("recurring", None)

    if model.created_at:
        data["created_at"] = iso_minutes(model.created_at)
    if model.completed_at:
        data["completed_at"] = iso_minutes(model.completed_at)

    if model.completed_by_username:
        data["completed_by"] = model.completed_by_username

    return data


def load_tasks() -> List[Dict[str, Any]]:
    if not DB_ENABLED or SessionLocal is None:
        records = load_json(TASKS_FILE, [])
        return [apply_task_defaults(dict(task)) for task in records]
    with SessionLocal() as session:
        rows = (
            session.query(TaskModel)
            .order_by(TaskModel.position, TaskModel.id)
            .all()
        )
        if not rows:
            records = load_json(TASKS_FILE, [])
            return [apply_task_defaults(dict(task)) for task in records]
        return [apply_task_defaults(_task_to_dict(row)) for row in rows]


def save_tasks(tasks: List[Dict[str, Any]]):
    tasks = tasks or []
    normalized: List[Dict[str, Any]] = []
    for record in tasks:
        if not isinstance(record, dict):
            continue
        item = dict(record)
        item["tags"] = normalize_tags(item.get("tags"))
        item["recurring"] = normalize_recurring(item.get("recurring"))
        normalized.append(item)

    if not DB_ENABLED or SessionLocal is None:
        save_json(TASKS_FILE, normalized)
        return

    with SessionLocal.begin() as session:
        session.execute(delete(TaskModel))
        session.flush()
        valid_usernames = {row.username for row in session.query(UserModel).all()}
        for idx, item in enumerate(normalized):
            assigned_username = _optional_username(item.get("assigned_username"))
            owner_username = _optional_username(item.get("owner") or item.get("created_by"))
            completed_by_username = _optional_username(item.get("completed_by"))

            if assigned_username and assigned_username not in valid_usernames:
                assigned_username = None
            if owner_username and owner_username not in valid_usernames:
                owner_username = None
            if completed_by_username and completed_by_username not in valid_usernames:
                completed_by_username = None

            due_date = parse_date(item.get("due_date")) if item.get("due_date") else None
            created_at = parse_dt_any(item.get("created_at")) or datetime.utcnow()
            completed_at = parse_dt_any(item.get("completed_at")) if item.get("completed_at") else None

            priority = item.get("priority") or "Medium"
            recurring = normalize_recurring(item.get("recurring"))
            notes = item.get("notes") or None
            assigned_display = item.get("assigned_to") or None
            overdue = bool(item.get("overdue", False))
            extra = {k: v for k, v in item.items() if k not in TASK_PERSISTED_KEYS}

            if item.get("tags") is not None:
                extra["tags"] = item.get("tags") or []

            task = TaskModel(
                text=item.get("text") or "",
                done=bool(item.get("done", False)),
                priority=priority,
                notes=notes,
                due_date=due_date,
                recurring=recurring,
                created_at=created_at,
                completed_at=completed_at,
                overdue=overdue,
                assigned_username=assigned_username,
                assigned_display=assigned_display,
                owner_username=owner_username,
                completed_by_username=completed_by_username,
                position=idx,
                extra=extra or None,
            )
            session.add(task)

def load_users() -> List[Dict[str, Any]]:
    if not DB_ENABLED or SessionLocal is None:
        return load_json(USERS_FILE, [])
    with SessionLocal() as session:
        rows = session.query(UserModel).order_by(UserModel.username).all()
        if not rows:
            return load_json(USERS_FILE, [])
        return [_user_to_dict(row) for row in rows]


def save_users(users: List[Dict[str, Any]]):
    if not DB_ENABLED or SessionLocal is None:
        save_json(USERS_FILE, users)
        return

    users = users or []
    normalized: Dict[str, Dict[str, Any]] = {}
    for entry in users:
        if not isinstance(entry, dict):
            continue
        uname = _norm(entry.get("username"))
        if not uname:
            continue
        data = dict(entry)
        data["username"] = uname
        normalized[uname] = data

    with SessionLocal.begin() as session:
        existing = {row.username: row for row in session.query(UserModel).all()}
        for uname, data in normalized.items():
            titles = list(data.get("titles") or [])
            role = (data.get("role") or "member").lower()
            extra = {k: v for k, v in data.items() if k not in USER_PERSISTED_KEYS}
            join_raw = data.get("join_date")
            if isinstance(join_raw, datetime):
                join_date = join_raw
            elif isinstance(join_raw, date):
                join_date = datetime.combine(join_raw, datetime.min.time())
            elif join_raw:
                parsed_join = parse_dt_any(str(join_raw))
                join_date = parsed_join if parsed_join else None
            else:
                join_date = None
            if not join_date:
                join_date = datetime.utcnow()
            total_completed = int(data.get("total_tasks_completed") or 0)
            streak_value = int(data.get("streak_count") or 0)

            record = existing.pop(uname, None)
            if record:
                record.display_name = data.get("display_name")
                record.password_hash = data.get("password")
                record.role = role
                record.titles = titles
                record.join_date = join_date
                record.total_tasks_completed = total_completed
                record.streak_count = streak_value
                record.extra = extra or None
            else:
                session.add(
                    UserModel(
                        username=uname,
                        display_name=data.get("display_name"),
                        password_hash=data.get("password"),
                        role=role,
                        titles=titles,
                        join_date=join_date,
                        total_tasks_completed=total_completed,
                        streak_count=streak_value,
                        extra=extra or None,
                    )
                )

        for stale in existing.values():
            session.delete(stale)


def find_user_record(username: str | None):
    if not username:
        return None
    uname = _norm(username)
    if not uname:
        return None

    if DB_ENABLED and SessionLocal is not None:
        with SessionLocal() as session:
            record = session.get(UserModel, uname)
            if record:
                return _user_to_dict(record)
            return None

    for record in load_json(USERS_FILE, []):
        if _norm(record.get("username")) == uname:
            return record
    return None


@login_manager.user_loader
def load_logged_in_user(user_id: str):
    record = find_user_record(user_id)
    if record:
        return AppUser.from_record(record)
    return None

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

def seed_demo_data():
    if not DEMO:
        return

    users = load_users()
    tasks = load_tasks()
    groups = load_groups()
    if users or tasks or groups:
        return

    admin_pw_source = os.getenv("DEMO_ADMIN_PASSWORD") or secrets.token_urlsafe(24)
    member_pw_source = os.getenv("DEMO_MEMBER_PASSWORD") or secrets.token_urlsafe(24)

    admin_pw = generate_password_hash(admin_pw_source)
    member_pw = generate_password_hash(member_pw_source)

    demo_users = [
        {
            "username": "demo_admin",
            "display_name": "Demo Admin",
            "password": admin_pw,
            "role": "manager",
            "titles": ["Manager"],
        },
        {
            "username": "demo_member",
            "display_name": "Demo Member",
            "password": member_pw,
            "role": "member",
            "titles": ["Team Member"],
        },
    ]

    now_stamp = iso_minutes(datetime.now())
    due_date = (date.today() + timedelta(days=3)).isoformat()

    demo_tasks = [
        {
            "text": "Review the demo dashboard",
            "done": False,
            "priority": "High",
            "assigned_to": "Demo Member",
            "assigned_username": "demo_member",
            "notes": "Walk through the tasks list and complete onboarding.",
            "created_by": "demo_admin",
            "created_at": now_stamp,
            "due_date": due_date,
        },
        {
            "text": "Share feedback in group chat",
            "done": False,
            "priority": "Medium",
            "assigned_to": "Demo Admin",
            "assigned_username": "demo_admin",
            "notes": "Post an update in the demo team channel.",
            "created_by": "demo_admin",
            "created_at": now_stamp,
            "due_date": due_date,
        },
    ]

    demo_group_id = str(uuid.uuid4())
    demo_groups = [
        {
            "id": demo_group_id,
            "name": "Demo Team",
            "supervisor": "demo_admin",
            "members": ["demo_admin", "demo_member"],
        }
    ]

    demo_group_tasks = {
        demo_group_id: [
            {
                "text": "Welcome new teammates",
                "priority": "Medium",
                "recurring": "none",
                "notes": "Keep this message pinned for future demos.",
                "done": False,
                "created_by": "demo_admin",
                "created_at": now_stamp,
            }
        ]
    }

    demo_group_messages = {
        demo_group_id: [
            {
                "sender": "demo_admin",
                "timestamp": now_stamp,
                "text": "Thanks for trying the demo! Explore the sidebar to see everything in action.",
                "image": None,
                "pinned": True,
            },
            {
                "sender": "demo_member",
                "timestamp": now_stamp,
                "text": "Got it! I'll start with the task list and calendar.",
                "image": None,
                "pinned": False,
            },
        ]
    }

    demo_group_seen = {
        "demo_admin": {demo_group_id: now_stamp},
    }

    save_users(demo_users)
    save_tasks(demo_tasks)
    save_groups(demo_groups)
    save_group_tasks(demo_group_tasks)
    save_group_messages(demo_group_messages)
    save_group_seen(demo_group_seen)

seed_demo_data()


@app.get("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}

@app.get("/healthz")
def healthz():
    return {"ok": True, "mode": APP_MODE}, 200

# ------------------------------- Jinja filters -------------------------------
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

# ------------------------------- Auth -------------------------------
@app.route("/signup", methods=["GET","POST"])
@demo_guard
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
            "titles": [],
            "join_date": datetime.utcnow().isoformat(timespec="seconds"),
            "total_tasks_completed": 0,
            "streak_count": 0
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
        record = find_user_record(uname)
        if record and check_password_hash(record["password"], pwd):
            login_user(AppUser.from_record(record))
            flash("Logged in.")
            return redirect(url_for("index"))
        flash("Invalid credentials.")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    # The "Are you sure?" confirmation is implemented in templates via onclick confirm()
    logout_user()
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("login"))

# ------------------------------- Forgot / Reset Password ---------------------
@app.route("/forgot", methods=["GET", "POST"])
@demo_guard
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
@demo_guard
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

# ------------------------------- Home / Dashboard -----------------------------
@app.route("/", endpoint="dashboard")
@login_required
def index():
    username = require_username()
    role = current_role()
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
        t for t in tasks_all if task_visible_to(t, username, users)
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

# ------------------------------- Reminders -------------------------------
@app.route("/reminders/add", methods=["POST"], endpoint="add_reminder")
@login_required
@demo_guard
def reminders_add():
    text = request.form.get("text","").strip()
    due  = request.form.get("due_at","").strip()
    if not text:
        flash("Reminder text required.")
        return redirect(url_for("index"))
    items = load_reminders()
    username = require_username()
    items.append({
        "id": str(uuid.uuid4()),
        "user": username,
        "text": text,
        "due_at": due,
        "done": False
    })
    save_reminders(items)
    flash("Reminder added.")
    return redirect(url_for("index"))

@app.route("/reminders/<rid>/delete", methods=["POST"], endpoint="reminders_delete")
@login_required
@demo_guard
def reminders_delete(rid):
    items = load_reminders()
    username = require_username()
    items = [r for r in items if not (r.get("id")==rid and r.get("user")==username)]
    save_reminders(items)
    flash("Reminder deleted.")
    return redirect(url_for("index"))

@app.route("/reminders/<rid>/toggle", methods=["POST"])
@login_required
@demo_guard
def reminders_toggle(rid):
    items = load_reminders()
    username = require_username()
    for r in items:
        if r.get("id")==rid and r.get("user")==username:
            r["done"] = not r.get("done", False)
            break
    save_reminders(items)
    return redirect(url_for("index"))

# ------------------------------- Global Search -------------------------------
@app.route("/search")
@login_required
def search():
    q = request.args.get("q","").strip().lower()
    user = require_username()
    role = current_role()

    users = load_users()
    uname_to_disp = {u["username"]: u.get("display_name") or u["username"].title() for u in users}

    # tasks (respect visibility)
    ts_all = load_tasks()
    if role != "manager":
        ts_all = [t for t in ts_all if task_visible_to(t, user, users)]
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

# ------------------------------- Task CRUD / Manager Pages -------------------
@app.route("/add", methods=["POST"])
@login_required
@demo_guard
def add():
    text = request.form.get("task","").strip()
    if not text:
        flash("Task text required.")
        return redirect(url_for("tasks_page" if current_role()=="manager" else "index"))

    priority = request.form.get("priority","Medium")
    assignee_raw = request.form.get("assigned_to","").strip()
    assignee_key = _norm(assignee_raw)
    due_date = request.form.get("due_date","").strip()
    recurring = normalize_recurring(request.form.get("recurring"))
    notes = request.form.get("notes","").strip()
    tags = normalize_tags(request.form.get("tags"))
    created_by = require_username()

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
        "tags": tags,
        "owner": created_by,
        "created_by": created_by,
        "created_at": datetime.now().isoformat(timespec="minutes")
    }
    ts = load_tasks()
    ts.append(new)
    save_tasks(ts)
    flash("Task added.")
    return redirect(url_for("tasks_page" if current_role()=="manager" else "index"))

@app.route("/toggle/<int:task_id>", methods=["POST"])
@login_required
@demo_guard
def toggle(task_id):
    username = require_username()
    role = current_role()
    users = load_users()
    ts = load_tasks()
    newly_awarded: List[Dict[str, Any]] = []
    if 0 <= task_id < len(ts):
        t = ts[task_id]
        if role == "manager" or task_visible_to(t, username, users):
            t["done"] = not t.get("done", False)
            if t["done"]:
                now = datetime.now()
                t["completed_at"] = iso_minutes(now)
                t["completed_by"] = username
                recurring = normalize_recurring(t.get("recurring"))
                if recurring and t.get("due_date"):
                    due_date = parse_date(t.get("due_date"))
                    next_due = next_recurring_due_date(due_date, recurring) if due_date else None
                    if next_due:
                        new_task = dict(t)
                        for key in ("completed_at", "completed_by"):
                            new_task.pop(key, None)
                        new_task["done"] = False
                        new_task["overdue"] = False
                        new_task["created_at"] = iso_minutes(now)
                        new_task["due_date"] = next_due.isoformat()
                        ts.append(new_task)
                newly_awarded = award_badges_for_user(username, ts)
            else:
                t.pop("completed_at", None)
                t.pop("completed_by", None)
    save_tasks(ts)
    for badge in newly_awarded:
        description = badge.get("description") or ""
        if description:
            flash(f"Badge unlocked: {badge.get('name')} - {description}")
        else:
            flash(f"Badge unlocked: {badge.get('name')}")
    flash("Task status updated.")
    return redirect(url_for("tasks_page" if role == "manager" else "index"))

@app.route("/remove/<int:task_id>")
@login_required
def remove(task_id):
    username = require_username()
    role = current_role()
    users = load_users()
    ts = load_tasks()
    if 0<=task_id<len(ts):
        t = ts[task_id]
        if role=="manager" or task_visible_to(t, username, users):
            ts.pop(task_id)
            save_tasks(ts)
            flash("Task removed.")
        else:
            flash("Not authorized to remove this task.")
    return redirect(url_for("tasks_page" if role=="manager" else "index"))

@app.route("/edit/<int:task_id>", methods=["GET","POST"])
@login_required
@demo_guard
def edit(task_id):
    username = require_username()
    role = current_role()
    users = load_users()
    ts = load_tasks()

    if request.method=="POST":
        if 0<=task_id<len(ts):
            t = ts[task_id]
            if role=="manager" or task_visible_to(t, username, users):
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
                t["tags"] = normalize_tags(request.form.get("tags"))
                t["recurring"] = normalize_recurring(request.form.get("recurring"))
                save_tasks(ts)
                flash("Task updated.")
        return redirect(url_for("tasks_page" if role=="manager" else "index"))
    else:
        if 0<=task_id<len(ts):
            t = ts[task_id]
            if role=="manager" or task_visible_to(t, username, users):
                assignable = [
                    {"username": u["username"], "display_name": u.get("display_name") or u["username"].title()}
                    for u in users
                    if u.get("role")!="manager"
                ]
                return render_template("edit.html", task=t, task_id=task_id, assignable_users=assignable, role=role)
    return redirect(url_for("tasks_page" if role=="manager" else "index"))

@app.route("/tasks", endpoint="task_manager")
@manager_required
def tasks_page():
    sort_by = request.args.get("sort", "due_asc")
    all_tasks = load_tasks()

    priority_lookup = {"high": "High", "medium": "Medium", "low": "Low"}
    selected_priorities: List[str] = []
    for value in request.args.getlist("priority"):
        key = value.strip().lower()
        if key in priority_lookup:
            selected_priorities.append(priority_lookup[key])

    tag_filters_raw = [tag.strip() for tag in request.args.getlist("tags") if tag.strip()]
    selected_tag_slugs = [tag.lower() for tag in tag_filters_raw]

    due_bucket = (request.args.get("due_bucket") or "").lower()
    due_from_raw = request.args.get("due_from", "").strip()
    due_to_raw = request.args.get("due_to", "").strip()
    due_from = parse_date(due_from_raw) if due_from_raw else None
    due_to = parse_date(due_to_raw) if due_to_raw else None

    today_val = date.today()

    filtered: List[Dict[str, Any]] = []
    for task in all_tasks:
        tags_lower = [tag.lower() for tag in task.get("tags", [])]
        due_ref = task.get("due_date") or task.get("due")
        due_dt = parse_date(due_ref) if due_ref else None

        if selected_priorities and task.get("priority") not in selected_priorities:
            continue
        if selected_tag_slugs and not any(tag in tags_lower for tag in selected_tag_slugs):
            continue

        if due_bucket == "overdue":
            if not (due_dt and due_dt < today_val and not task.get("done")):
                continue
        elif due_bucket == "today":
            if not (due_dt and due_dt == today_val):
                continue
        elif due_bucket == "upcoming":
            if not (due_dt and due_dt > today_val):
                continue
        elif due_bucket == "week":
            if not (due_dt and 0 <= (due_dt - today_val).days <= 7):
                continue
        elif due_bucket == "none":
            if due_dt is not None:
                continue

        if due_from and (due_dt is None or due_dt < due_from):
            continue
        if due_to and (due_dt is None or due_dt > due_to):
            continue

        filtered.append(task)

    valid_sorts = {"due_asc", "due_desc", "priority_hl", "priority_lh", "completed", "created_desc"}
    if sort_by not in valid_sorts:
        sort_by = "due_asc"

    priority_rank = {"High": 0, "Medium": 1, "Low": 2}

    if sort_by == "completed":
        filtered = [task for task in filtered if task.get("done")]
        filtered.sort(key=lambda t: parse_dt_any(t.get("completed_at")) or datetime.min, reverse=True)
    elif sort_by == "priority_hl":
        filtered.sort(key=lambda t: priority_rank.get(t.get("priority"), len(priority_rank)))
    elif sort_by == "priority_lh":
        filtered.sort(key=lambda t: priority_rank.get(t.get("priority"), len(priority_rank)), reverse=True)
    elif sort_by == "due_desc":
        filtered.sort(key=lambda t: parse_date_any(t.get("due") or t.get("due_date"), default_far=True), reverse=True)
    elif sort_by == "created_desc":
        filtered.sort(key=lambda t: parse_dt_any(t.get("created_at")) or datetime.min, reverse=True)
    else:
        filtered.sort(key=lambda t: parse_date_any(t.get("due") or t.get("due_date"), default_far=True))

    users = load_users()
    elig = {"assistant manager", "family swim supervisor", "lead supervisor", "swim administrator", "programming supervisor", "supervisor"}
    assignable = [
        {"username": u["username"], "display_name": u.get("display_name") or u["username"].title()}
        for u in users
        if u["role"] != "manager" and any(t.lower() in elig for t in u.get("titles", []))
    ]

    all_tags = sorted({tag for task in all_tasks for tag in normalize_tags(task.get("tags"))})

    return render_template("task_manager.html",
                           tasks=filtered,
                           role="manager",
                           sort_by=sort_by,
                           assignable_users=assignable,
                           selected_priorities=selected_priorities,
                           tag_filters=tag_filters_raw,
                           selected_tag_slugs=selected_tag_slugs,
                           due_bucket=due_bucket,
                           due_from=due_from_raw,
                           due_to=due_to_raw,
                           all_tags=all_tags,
                           total_tasks=len(all_tasks),
                           visible_tasks=len(filtered))
@app.route("/tasks/create", methods=["GET","POST"])
@manager_required
@demo_guard
def create_task_page():
    if request.method=="POST":
        return redirect(url_for("add"))
    users = load_users()
    elig = {"assistant manager","family swim supervisor","lead supervisor","swim administrator","programming supervisor","supervisor"}
    assignable = [
        {"username": u["username"], "display_name": u.get("display_name") or u["username"].title()}
        for u in users
        if u["role"]!="manager" and any(t.lower() in elig for t in u.get("titles",[]))
    ]
    return render_template("create_task.html", assignable_users=assignable, all_tags=sorted({tag for task in load_tasks() for tag in normalize_tags(task.get("tags"))}))

# ------------------------------- Shifts -------------------------------
@app.route("/shifts", endpoint="shifts")
@manager_required
def view_shifts():
    return render_template("shifts.html", shifts=load_shifts())

@app.route("/shifts/add", methods=["GET","POST"])
@manager_required
@demo_guard
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
    u = require_username()
    sh = [s for s in load_shifts() if _norm(s.get("assigned_to"))==_norm(u)]
    return render_template("my_shifts.html", shifts=sh)

# ------------------------------- Team / Titles -------------------------------
@app.route("/members", endpoint="team_manager")
@manager_required
def team_member_manager():
    return render_template("team_manager.html", users=load_users())

@app.route("/titles", methods=["GET","POST"])
@manager_required
@demo_guard
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
@demo_guard
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

# ------------------------------- Calendar -------------------------------
@app.route("/calendar", endpoint="calendar")
@login_required
def calendar_view():
    return render_template("calendar.html", role=current_role())


# New unified feed (tasks + shifts), supports ?scope=my|all
@app.route("/api/calendar")
@login_required
def calendar_feed():
    raw_scope = (request.args.get("scope") or "my").lower()
    if raw_scope not in {"my", "all"}:
        raw_scope = "my"

    username = require_username()
    role = current_role()
    show_all = raw_scope == "all" and role == "manager"

    users = load_users()
    tasks_all = load_tasks()

    now = datetime.utcnow()
    today = date.today()
    soon_cutoff = now + timedelta(hours=24)

    color_map = {
        "completed": "#2FA77A",
        "due_soon": "#F3B43E",
        "overdue": "#E6492D",
        "upcoming": "#4C6EF5",
    }
    text_color_map = {
        "completed": "#0B5132",
        "due_soon": "#66460A",
        "overdue": "#FFFFFF",
        "upcoming": "#FFFFFF",
    }

    events: List[Dict[str, Any]] = []

    for idx, task in enumerate(tasks_all):
        if not show_all and not task_visible_to(task, username, users):
            continue

        due_raw = task.get("due_date") or task.get("due")
        if not due_raw:
            continue

        due_dt = parse_dt_any(due_raw)
        due_date_only = parse_date(due_raw)
        if not due_dt and due_date_only:
            due_dt = datetime.combine(due_date_only, datetime.min.time())

        if not due_dt:
            continue

        all_day = due_dt.time() == datetime.min.time()
        done = bool(task.get("done"))

        status = "upcoming"
        if done:
            status = "completed"
        else:
            if due_date_only and due_date_only < today:
                status = "overdue"
            else:
                comparison_dt = due_dt if not all_day else datetime.combine(due_dt.date(), datetime.max.time())
                if comparison_dt <= soon_cutoff:
                    status = "due_soon"

        title = task.get("text") or "Task"
        recurring = normalize_recurring(task.get("recurring"))
        if recurring:
            title = f"{title} (Repeats {recurring.title()})"

        start_value = due_dt.isoformat() if not all_day else due_dt.date().isoformat()

        events.append({
            "id": f"task-{idx}",
            "title": title,
            "start": start_value,
            "allDay": all_day,
            "backgroundColor": color_map[status],
            "borderColor": color_map[status],
            "textColor": text_color_map.get(status),
            "classNames": [f"task-status-{status}"],
            "editable": not done,
            "durationEditable": False,
            "extendedProps": {
                "type": "task",
                "task_id": idx,
                "status": status,
                "done": done,
                "priority": task.get("priority") or "Medium",
                "assigned_to": task.get("assigned_to"),
                "assigned_username": task.get("assigned_username"),
                "notes": task.get("notes") or "",
                "due_raw": due_raw,
                "completed_at": task.get("completed_at"),
                "recurring": recurring,
                "edit_url": url_for("edit", task_id=idx),
            },
        })

    # shifts remain available for reference when permitted
    shifts_all = load_shifts()
    shifts = shifts_all if show_all else [
        s for s in shifts_all if _norm(s.get("assigned_to")) == _norm(username)
    ]
    for j, shift in enumerate(shifts):
        shift_day = parse_date(shift.get("date"))
        if not shift_day:
            continue
        events.append({
            "id": f"shift-{j}",
            "title": f"Shift {shift.get('start_time', '')} - {shift.get('end_time', '')}",
            "start": shift_day.isoformat(),
            "allDay": True,
            "backgroundColor": "#6C7AE0",
            "borderColor": "#6C7AE0",
            "textColor": "#FFFFFF",
            "extendedProps": {"type": "shift"},
            "editable": False,
        })

    return jsonify(events)

@app.route("/api/tasks/<int:task_id>/due", methods=["PATCH"])
@login_required
@demo_guard
def update_task_due_date(task_id: int):
    username = require_username()
    role = current_role()
    tasks = load_tasks()

    if not (0 <= task_id < len(tasks)):
        return jsonify({"error": "Task not found"}), 404

    task = tasks[task_id]
    users = load_users()
    if role != "manager" and not task_visible_to(task, username, users):
        return jsonify({"error": "Not authorized"}), 403

    payload = request.get_json(silent=True) or {}
    due_raw = payload.get("due_date")
    all_day = bool(payload.get("all_day"))
    if not due_raw:
        return jsonify({"error": "Missing due_date"}), 400

    due_dt = parse_dt_any(due_raw)
    if not due_dt:
        due_date_only = parse_date(due_raw)
        if due_date_only:
            due_dt = datetime.combine(due_date_only, datetime.min.time())
    if not due_dt:
        return jsonify({"error": "Invalid due_date"}), 400

    due_dt = due_dt.replace(second=0, microsecond=0)
    new_due = due_dt.date().isoformat() if all_day else due_dt.isoformat(timespec="minutes")

    task["due_date"] = new_due
    task["due"] = new_due

    if not task.get("done"):
        due_date_only = parse_date(new_due)
        task["overdue"] = bool(due_date_only and due_date_only < date.today())

    save_tasks(tasks)
    return jsonify({"ok": True, "due_date": new_due})


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

# ------------------------------- Settings -------------------------------
@app.route("/settings", methods=["GET"])
@login_required
def settings():
    user = require_username()

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
@demo_guard
def settings_update():
    user = require_username()

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

@app.route("/settings/notifications", methods=["GET", "POST"])
@login_required
@demo_guard
def notifications_settings():
    username = require_username()
    notif_settings = load_notification_settings_for(username)

    frequency_choices = [("daily", "Daily"), ("weekly", "Weekly"), ("off", "Off")]
    channel_choices = [("email", "Email"), ("discord", "Discord webhook")]
    weekday_choices = NOTIFICATION_WEEKDAYS

    record = find_user_record(username) or {}
    contact_email = record.get("email") or ""
    discord_webhook = record.get("discord_webhook") or ""

    if request.method == "POST":
        updated = dict(notif_settings)
        freq = request.form.get("frequency", updated["frequency"]).lower()
        if freq not in NOTIFICATION_FREQUENCIES:
            freq = updated["frequency"]
        updated["frequency"] = freq

        updated["channels"] = normalize_notification_channels(request.form.getlist("channels"))

        try:
            updated["daily_hour"] = max(0, min(23, int(request.form.get("daily_hour", updated["daily_hour"]))))
        except (TypeError, ValueError):
            pass
        try:
            updated["weekly_day"] = max(0, min(6, int(request.form.get("weekly_day", updated["weekly_day"]))))
        except (TypeError, ValueError):
            pass

        updated["summary_enabled"] = bool(request.form.get("summary_enabled"))
        updated["overdue_enabled"] = bool(request.form.get("overdue_enabled"))
        updated["badge_enabled"] = bool(request.form.get("badge_enabled"))

        persist_notification_settings(username, updated)

        email_value = request.form.get("email", "").strip()
        discord_value = request.form.get("discord_webhook", "").strip()
        users = load_users()
        for user in users:
            if _norm(user.get("username")) != username:
                continue
            if email_value:
                user["email"] = email_value
            else:
                user.pop("email", None)
            if discord_value:
                user["discord_webhook"] = discord_value
            else:
                user.pop("discord_webhook", None)
            save_users(users)
            break

        flash("Notification preferences updated.")
        return redirect(url_for("notifications_settings"))

    return render_template(
        "settings_notifications.html",
        settings=notif_settings,
        frequency_choices=frequency_choices,
        channel_choices=channel_choices,
        weekday_choices=weekday_choices,
        contact_email=contact_email,
        discord_webhook=discord_webhook,
    )


# ------------------------------- Assistant -------------------------------
@app.route("/assistant", methods=["GET", "POST"], endpoint="assistant")
@login_required
def assistant_view():
    username = require_username()
    history = load_assistant_history()
    convo = history.get(username, [])

    if request.method == "POST":
        prompt = (request.form.get("prompt") or "").strip()
        if not prompt:
            flash("Ask me something to get started!")
            return redirect(url_for("assistant"))

        timestamp = datetime.utcnow().isoformat(timespec="seconds")
        convo.append({"role": "user", "content": prompt, "timestamp": timestamp})

        reply = generate_assistant_reply(username, prompt)
        convo.append({"role": "assistant", "content": reply, "timestamp": datetime.utcnow().isoformat(timespec="seconds")})

        # Keep the last 40 entries per user
        history[username] = convo[-40:]
        save_assistant_history(history)
        return redirect(url_for("assistant"))

    stats = get_user_completion_stats(username, load_tasks())
    badge_catalog = get_badge_catalog()
    earned = get_user_badges(username)
    earned_slugs = {badge.get("slug") for badge in earned}
    progress = get_next_badge_progress(stats, earned_slugs, badge_catalog)

    return render_template(
        "assistant.html",
        messages=convo,
        progress=progress,
        stats=stats,
    )


@app.route("/theme", methods=["POST"], endpoint="theme_update")
def theme_update():
    payload = request.get_json(silent=True) or {}
    theme = str(payload.get("theme", "")).lower()
    if theme not in {"light", "dark"}:
        return jsonify({"error": "invalid theme"}), 400
    session["theme"] = theme
    if current_user.is_authenticated:
        username = require_username()
        prefs_all = load_prefs()
        if not isinstance(prefs_all, dict):
            prefs_all = {}
        record = prefs_all.get(username, {}) or {}
        record["theme"] = theme
        prefs_all[username] = record
        save_prefs(prefs_all)
    return jsonify({"theme": theme})

# ------------------------------- Badges -------------------------------


@app.route("/profile")
@login_required
def profile():
    username = require_username()
    user_record = find_user_record(username)
    if not user_record:
        abort(404)

    raw_join = user_record.get("join_date")
    join_dt: datetime | None = None
    if isinstance(raw_join, datetime):
        join_dt = raw_join
    elif isinstance(raw_join, date):
        join_dt = datetime.combine(raw_join, datetime.min.time())
    elif raw_join:
        join_dt = parse_dt_any(str(raw_join))
    if not join_dt:
        join_dt = datetime.utcnow()
        user_record["join_date"] = join_dt.isoformat(timespec="seconds")
    join_date_display = join_dt.date()

    tasks = load_tasks()
    stats = get_user_completion_stats(username, tasks)
    total_completed = stats.get("completed_count", 0)
    current_streak = stats.get("current_streak", 0)
    longest_streak = stats.get("longest_streak", 0)
    user_record["total_tasks_completed"] = total_completed
    user_record["streak_count"] = current_streak

    earned_badges = get_user_badges(username)
    badge_catalog = get_badge_catalog()
    earned_slugs = {badge.get("slug") for badge in earned_badges}
    locked_badges = [dict(badge_catalog[slug]) for slug in badge_catalog if slug not in earned_slugs]
    locked_badges.sort(key=lambda badge: (badge.get("name") or "").lower())

    progress_info = get_next_badge_progress(stats, earned_slugs, badge_catalog)

    update_user_progress_fields(username, total_completed, current_streak)

    return render_template(
        "profile.html",
        user=user_record,
        join_date=join_date_display,
        stats=stats,
        total_completed=total_completed,
        current_streak=current_streak,
        longest_streak=longest_streak,
        progress_info=progress_info,
        earned_badges=earned_badges,
        locked_badges=locked_badges,
    )


@app.route("/badges", endpoint="my_badges")
@login_required
def my_badges():
    username = require_username()
    earned = get_user_badges(username)
    catalog = get_badge_catalog()
    earned_slugs = {badge.get("slug") for badge in earned}
    locked = [badge for slug, badge in catalog.items() if slug not in earned_slugs]
    locked.sort(key=lambda badge: badge.get("name") or "")
    return render_template("my_badges.html", earned_badges=earned, locked_badges=locked)


# ------------------------------- Overdue -------------------------------
@app.route("/overdue", endpoint="overdue")
@login_required
def overdue_tasks():
    username = require_username()
    role = current_role()
    today = date.today()

    users = load_users()
    tasks = load_tasks()

    overdue_entries = []
    for idx, task in enumerate(tasks):
        if task.get("done"):
            continue

        if role != "manager" and not task_visible_to(task, username, users):
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

# ------------------------------- Group Chats -------------------------------
@app.route("/groups", methods=["GET", "POST"])
@manager_required
@demo_guard
def group_chat_manager():
    groups = load_groups()
    users  = load_users()
    supervisors = [u for u in users if any(t.lower()=="supervisor" for t in u.get("titles",[]))]

    if request.method == "POST":
        name = (request.form.get("group_name") or "").strip()
        supervisor = (request.form.get("supervisor") or "").strip().lower()

        if not name or not supervisor:
            flash("Group name and supervisor are required.")
        else:
            user_lookup = {u["username"].lower(): u for u in users}
            supervisor_user = user_lookup.get(supervisor)
            if supervisor_user is None:
                flash("Selected supervisor no longer exists.")
            elif any((g.get("name") or "").strip().lower() == name.lower() for g in groups):
                flash("A group with that name already exists.")
            else:
                new_group = {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "supervisor": supervisor,
                    "members": [supervisor],
                }
                groups.append(new_group)
                save_groups(groups)
                flash("Group created.")
                return redirect(url_for("group_chat_manager"))

    return render_template("group_chat_manager.html",
                           groups=groups,
                           supervisors=supervisors,
                           users=users)

# Member-facing chats hub (non-managers list only their groups)
@app.route("/chats", endpoint="my_chats")
@login_required
def chats():
    user = require_username()
    role = current_role()
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
    user = require_username()
    role = current_role()
    gs   = load_groups()
    grp  = next((g for g in gs if g["id"]==group_id), None)
    if not grp or (role!="manager" and user not in grp.get("members",[])):
        # Non-members/managers: bounce to the appropriate hub
        return redirect(url_for("group_chat_manager" if role=="manager" else "my_chats"))

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
@demo_guard
def groups_mark_all_read():
    user = require_username()
    role = current_role()
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
@demo_guard
def post_group_message(group_id):
    text = request.form.get("message","").strip()
    img  = request.files.get("image")

    # membership (managers can always post)
    user = require_username()
    role = current_role()
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
@demo_guard
def pin_message(group_id, idx):
    user = require_username()
    role = current_role()
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
@demo_guard
def delete_message(group_id, idx):
    user = require_username()
    role = current_role()

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
@demo_guard
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
@demo_guard
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
@demo_guard
def add_group_task(group_id):
    text     = request.form.get("text","").strip()
    priority = request.form.get("priority","Medium")
    notes    = request.form.get("notes","").strip()
    if not text:
        flash("Task text required.")
        return redirect(url_for("view_group",group_id=group_id))

    # members or managers only
    user = require_username()
    role = current_role()
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
@demo_guard
def toggle_group_task(group_id, idx):
    user = require_username()
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

LEGACY_ENDPOINT_ALIASES = [
    ("/", "dashboard", "index"),
    ("/calendar", "calendar", "calendar_view"),
    ("/overdue", "overdue", "overdue_tasks"),
    ("/chats", "my_chats", "chats"),
    ("/tasks", "task_manager", "tasks_page"),
    ("/members", "team_manager", "team_member_manager"),
    ("/shifts", "shifts", "view_shifts"),
]

for rule, canonical, legacy in LEGACY_ENDPOINT_ALIASES:
    if canonical in app.view_functions and legacy not in app.view_functions:
        app.add_url_rule(rule, endpoint=legacy, view_func=app.view_functions[canonical])


def notification_defaults() -> dict:
    return dict(NOTIFICATION_DEFAULTS)


def normalize_notification_channels(channels) -> list[str]:
    normalized: list[str] = []
    if not channels:
        return list(NOTIFICATION_DEFAULTS.get("channels", ["email"]))
    for channel in channels:
        if not channel:
            continue
        slug = str(channel).strip().lower()
        if slug in NOTIFICATION_CHANNELS and slug not in normalized:
            normalized.append(slug)
    return normalized or list(NOTIFICATION_DEFAULTS.get("channels", ["email"]))


def load_notification_settings_for(username: str) -> dict:
    prefs_all = load_prefs()
    entry = prefs_all.get(username, {}) if isinstance(prefs_all, dict) else {}
    settings = notification_defaults()
    if isinstance(entry, dict):
        settings.update(entry.get("notifications") or {})
    settings["frequency"] = str(settings.get("frequency", "daily")).lower()
    settings["channels"] = normalize_notification_channels(settings.get("channels"))
    try:
        settings["daily_hour"] = max(0, min(23, int(settings.get("daily_hour", 7))))
    except (TypeError, ValueError):
        settings["daily_hour"] = NOTIFICATION_DEFAULTS.get("daily_hour", 7)
    try:
        settings["weekly_day"] = max(0, min(6, int(settings.get("weekly_day", 0))))
    except (TypeError, ValueError):
        settings["weekly_day"] = NOTIFICATION_DEFAULTS.get("weekly_day", 0)
    settings["summary_enabled"] = bool(settings.get("summary_enabled", True))
    settings["overdue_enabled"] = bool(settings.get("overdue_enabled", True))
    settings["badge_enabled"] = bool(settings.get("badge_enabled", True))
    return settings


def persist_notification_settings(username: str, new_settings: dict) -> None:
    prefs_all = load_prefs()
    current = prefs_all.get(username)
    if not isinstance(current, dict):
        current = {}
    current["notifications"] = new_settings
    prefs_all[username] = current
    save_prefs(prefs_all)


# ------------------------------- Assistant helpers -------------------------------

PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def _user_visible_tasks(username: str, users: list[dict] | None = None) -> list[dict]:
    users = users or load_users()
    tasks = []
    uname = _norm(username)
    if not uname:
        return tasks
    for task in load_tasks():
        if task_visible_to(task, uname, users):
            tasks.append(task)
    return tasks



def _format_task_summary(task: dict) -> str:
    title = task.get("text") or "Task"
    priority = task.get("priority") or "Medium"
    due = task.get("due_date") or task.get("due")
    due_label = f" due {due}" if due else ""
    return f"- {title} ({priority}{due_label})"



def generate_assistant_reply(username: str, prompt: str) -> str:
    prompt_lower = prompt.lower()
    users = load_users()
    tasks_all = load_tasks()
    visible_tasks = [task for task in tasks_all if task_visible_to(task, username, users)]
    open_tasks = [task for task in visible_tasks if not task.get("done")]

    stats = get_user_completion_stats(username, tasks_all)
    earned = get_user_badges(username)
    badge_catalog = get_badge_catalog()
    earned_slugs = {badge.get("slug") for badge in earned}
    progress = get_next_badge_progress(stats, earned_slugs, badge_catalog)

    lines: list[str] = []

    if "plan" in prompt_lower and "task" in prompt_lower:
        lines.append("Here is a focused plan for your next tasks:")

        def sort_key(task: dict):
            priority = PRIORITY_ORDER.get(task.get("priority"), 3)
            due_str = task.get("due_date") or task.get("due")
            due_dt = parse_dt_any(due_str) if due_str else None
            due_sort = due_dt or datetime.max
            return (priority, due_sort, task.get("text") or "")

        top_tasks = sorted(open_tasks, key=sort_key)[:3]
        if top_tasks:
            lines.extend(_format_task_summary(task) for task in top_tasks)
        else:
            lines.append("- No open tasks found. Maybe create a new one?")
        lines.append("")

    if ("summarize" in prompt_lower or "summary" in prompt_lower) and ("week" in prompt_lower or "accomplish" in prompt_lower):
        completed = []
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        for task in visible_tasks:
            if not task.get("done"):
                continue
            completed_at = parse_dt_any(task.get("completed_at") or "")
            if completed_at and completed_at >= seven_days_ago:
                completed.append(task)
        if completed:
            lines.append("Wins from the past 7 days:")
            for task in completed[:5]:
                lines.append(_format_task_summary(task))
            if len(completed) > 5:
                lines.append(f"... and {len(completed) - 5} more.")
        else:
            lines.append("It looks like no tasks were completed in the last week. Let's change that!")
        lines.append("")

    if "suggest" in prompt_lower and ("category" in prompt_lower or "tag" in prompt_lower):
        tag_counts: dict[str, int] = {}
        for task in visible_tasks:
            tags = task.get("tags") or []
            if isinstance(tags, list):
                for tag in tags:
                    if not tag:
                        continue
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        if tag_counts:
            sorted_tags = sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[:5]
            lines.append("Here are categories your tasks already hint at:")
            for tag, count in sorted_tags:
                lines.append(f"- {tag} (used {count} time{'s' if count != 1 else ''})")
            lines.append("Try grouping upcoming tasks with these or related tags for clarity.")
        else:
            lines.append("I don't see any existing tags yet. Consider themes like Planning, Follow-up, Admin, or Deep Work.")
        lines.append("")

    if not lines:
        lines.append("I'm here to help with planning, summaries, and organization. Ask me about your tasks or badges!")

    if progress and progress.get("remaining"):
        badge = progress.get("badge", {})
        remaining = int(progress["remaining"])
        lines.append("")
        lines.append(f"Badge insight: just {remaining} task{'s' if remaining != 1 else ''} to unlock {badge.get('name', 'your next badge')}!")
    else:
        lines.append("")
        lines.append("Nice work - you're all caught up on current badge goals!")

    return "\n".join(line for line in lines if line is not None)



# ------------- Run -------------
if __name__ == "__main__":
    app.run(debug=DEBUG)



