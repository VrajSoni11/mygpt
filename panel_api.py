"""Extra endpoints for the desktop panel (tasks, habits, reminders as JSON).

Hook it into main.py with two lines, right after  app = FastAPI(title="Jarvis"):
    import panel_api
    panel_api.setup(app)
"""
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB_PATH = Path(__file__).parent / "jarvis.db"
router = APIRouter(prefix="/api")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _norm_due(value):
    if not value:
        return None
    v = value.strip().replace("T", " ")[:16]
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v if " " in fmt else v[:10], fmt).strftime(fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="Bad date format")


# ---------- Tasks ----------
class TaskIn(BaseModel):
    title: str
    due: str | None = None


@router.get("/tasks")
def get_tasks(status: str = "all"):
    q, params = "SELECT id, title, due, status FROM tasks", ()
    if status in ("open", "done"):
        q, params = q + " WHERE status = ?", (status,)
    q += " ORDER BY (due IS NULL), due, id"
    with db() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


@router.post("/tasks")
def add_task(body: TaskIn):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Empty title")
    with db() as c:
        cur = c.execute(
            "INSERT INTO tasks (title, due, created) VALUES (?, ?, ?)",
            (title, _norm_due(body.due), datetime.now().isoformat(timespec="seconds")),
        )
    return {"id": cur.lastrowid}


@router.post("/tasks/{task_id}/toggle")
def toggle_task(task_id: int):
    with db() as c:
        c.execute(
            "UPDATE tasks SET status = CASE status WHEN 'open' THEN 'done' ELSE 'open' END WHERE id = ?",
            (task_id,),
        )
    return {"ok": True}


# ---------- Habits ----------
class HabitIn(BaseModel):
    name: str


def _streak(days: set) -> int:
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


@router.get("/habits")
def get_habits():
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=6)).isoformat()
    out = []
    with db() as c:
        for h in c.execute("SELECT id, name FROM habits ORDER BY name").fetchall():
            days = {r["day"] for r in c.execute("SELECT day FROM habit_logs WHERE habit_id = ?", (h["id"],))}
            out.append(
                {
                    "id": h["id"],
                    "name": h["name"],
                    "streak": _streak(days),
                    "week": sum(1 for d in days if d >= week_ago),
                    "total": len(days),
                    "done_today": today in days,
                }
            )
    return out


@router.post("/habits")
def add_habit(body: HabitIn):
    name = body.name.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Empty name")
    with db() as c:
        c.execute("INSERT OR IGNORE INTO habits (name) VALUES (?)", (name,))
    return {"ok": True}


@router.post("/habits/{habit_id}/log")
def log_today(habit_id: int):
    with db() as c:
        c.execute(
            "INSERT OR IGNORE INTO habit_logs (habit_id, day) VALUES (?, ?)",
            (habit_id, date.today().isoformat()),
        )
    return {"ok": True}


# ---------- Reminders ----------
@router.get("/reminders")
def get_reminders():
    with db() as c:
        rows = c.execute(
            "SELECT id, text, remind_at FROM reminders WHERE fired = 0 ORDER BY remind_at"
        ).fetchall()
    return [dict(r) for r in rows]


def setup(app):
    # The desktop app loads from a local file, so the browser engine needs CORS enabled.
    # Safe because the backend only listens on 127.0.0.1 (your own laptop).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)