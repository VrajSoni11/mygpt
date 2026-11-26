import inspect
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ---------- Settings ----------
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:4b"
HISTORY_LIMIT = 12      # recent messages the AI sees (lower = faster)
MAX_STEPS = 5           # max tool-calling rounds per message
NUM_CTX = 8192          # context window (tools + prompt need more than the 4096 default)
KEEP_ALIVE = "30m"      # keep the model loaded in RAM between messages

BASE = Path(__file__).parent
DB_PATH = BASE / "jarvis.db"
PROFILE_PATH = BASE / "profile.md"

app = FastAPI(title="Jarvis")


# ---------- Database ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL, ts TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, due TEXT,
                status TEXT NOT NULL DEFAULT 'open', created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS habit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL, day TEXT NOT NULL,
                UNIQUE(habit_id, day));
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL, remind_at TEXT NOT NULL,
                fired INTEGER NOT NULL DEFAULT 0);
            """
        )


init_db()


def now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def norm_datetime(value: str) -> str:
    """Accepts 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' and returns a clean string."""
    v = value.strip().replace("T", " ")[:16]
    for fmt, out in (("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M"), ("%Y-%m-%d", "%Y-%m-%d")):
        try:
            return datetime.strptime(v if " " in fmt else v[:10], fmt).strftime(out)
        except ValueError:
            continue
    raise ValueError("Use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' (24h).")


# ---------- Tools (the things Jarvis can DO) ----------
def add_task(title: str, due: str = None) -> str:
    if due:
        try:
            due = norm_datetime(due)
        except ValueError as e:
            return f"Error: {e}"
    with db() as c:
        cur = c.execute(
            "INSERT INTO tasks (title, due, created) VALUES (?, ?, ?)",
            (title, due, now_str()),
        )
    return f"Task #{cur.lastrowid} added: {title}" + (f" (due {due})" if due else "")


def list_tasks(status: str = "open") -> str:
    q, params = "SELECT id, title, due, status FROM tasks", ()
    if status in ("open", "done"):
        q, params = q + " WHERE status = ?", (status,)
    q += " ORDER BY (due IS NULL), due, id"
    with db() as c:
        rows = c.execute(q, params).fetchall()
    if not rows:
        return "No tasks."
    return "\n".join(
        f"#{r['id']} [{r['status']}] {r['title']}" + (f" (due {r['due']})" if r["due"] else "")
        for r in rows
    )


def complete_task(task: str) -> str:
    task = str(task).strip()
    with db() as c:
        row = None
        if task.isdigit():
            row = c.execute(
                "SELECT id, title FROM tasks WHERE id = ? AND status = 'open'", (int(task),)
            ).fetchone()
        if not row:
            row = c.execute(
                "SELECT id, title FROM tasks WHERE status = 'open' AND title LIKE ? ORDER BY id LIMIT 1",
                (f"%{task}%",),
            ).fetchone()
        if not row:
            return f"No open task found matching '{task}'."
        c.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (row["id"],))
    return f"Task #{row['id']} marked done: {row['title']}"


def _streak(c, habit_id: int) -> int:
    days = {r["day"] for r in c.execute("SELECT day FROM habit_logs WHERE habit_id = ?", (habit_id,))}
    d = date.today()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def log_habit(habit: str, on_date: str = None) -> str:
    name = habit.strip().lower()
    day = (on_date or date.today().isoformat())[:10]
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return "Error: on_date must be YYYY-MM-DD."
    with db() as c:
        c.execute("INSERT OR IGNORE INTO habits (name) VALUES (?)", (name,))
        hid = c.execute("SELECT id FROM habits WHERE name = ?", (name,)).fetchone()["id"]
        cur = c.execute("INSERT OR IGNORE INTO habit_logs (habit_id, day) VALUES (?, ?)", (hid, day))
        s = _streak(c, hid)
    if cur.rowcount == 0:
        return f"'{name}' was already logged for {day}. Current streak: {s} day(s)."
    return f"Logged '{name}' for {day}. Current streak: {s} day(s)."


def habit_stats(habit: str = None) -> str:
    week_ago = (date.today() - timedelta(days=6)).isoformat()
    with db() as c:
        if habit:
            habits = c.execute("SELECT id, name FROM habits WHERE name LIKE ?", (f"%{habit.strip().lower()}%",)).fetchall()
        else:
            habits = c.execute("SELECT id, name FROM habits ORDER BY name").fetchall()
        if not habits:
            return "No habits tracked yet."
        lines = []
        for h in habits:
            total = c.execute("SELECT COUNT(*) n FROM habit_logs WHERE habit_id = ?", (h["id"],)).fetchone()["n"]
            week = c.execute(
                "SELECT COUNT(*) n FROM habit_logs WHERE habit_id = ? AND day >= ?", (h["id"], week_ago)
            ).fetchone()["n"]
            lines.append(f"{h['name']}: streak {_streak(c, h['id'])} day(s), {week}/7 days this week, {total} total")
    return "\n".join(lines)


def set_reminder(text: str, remind_at: str) -> str:
    try:
        when = norm_datetime(remind_at)
    except ValueError as e:
        return f"Error: {e}"
    if " " not in when:
        when += " 09:00"
    with db() as c:
        cur = c.execute("INSERT INTO reminders (text, remind_at) VALUES (?, ?)", (text, when))
    return f"Reminder #{cur.lastrowid} set for {when}: {text}"


def list_reminders() -> str:
    with db() as c:
        rows = c.execute("SELECT id, text, remind_at FROM reminders WHERE fired = 0 ORDER BY remind_at").fetchall()
    if not rows:
        return "No pending reminders."
    return "\n".join(f"#{r['id']} {r['remind_at']}: {r['text']}" for r in rows)


def save_fact(fact: str) -> str:
    with db() as c:
        c.execute("INSERT INTO facts (fact, ts) VALUES (?, ?)", (fact, now_str()))
    return f"Saved to memory: {fact}"


TOOLS = {
    f.__name__: f
    for f in (add_task, list_tasks, complete_task, log_habit, habit_stats, set_reminder, list_reminders, save_fact)
}


def _s(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _schema(name: str, desc: str, props: dict = None, required: list = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []},
        },
    }


TOOL_SCHEMAS = [
    _schema("add_task", "Add a to-do task.",
            {"title": _s("Short task description"),
             "due": _s("Optional due: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' (24h)")}, ["title"]),
    _schema("list_tasks", "List tasks.", {"status": _s("'open' (default), 'done' or 'all'")}),
    _schema("complete_task", "Mark a task as done.",
            {"task": _s("Task id number, or part of its title")}, ["task"]),
    _schema("log_habit", "Record that the owner did a habit today or on a given date.",
            {"habit": _s("Habit name, e.g. 'gym'"),
             "on_date": _s("Optional date YYYY-MM-DD, default today")}, ["habit"]),
    _schema("habit_stats", "Show habit streaks and counts. Omit habit for all habits.",
            {"habit": _s("Optional habit name")}),
    _schema("set_reminder", "Set a reminder at an exact date and time.",
            {"text": _s("What to remind about"),
             "remind_at": _s("'YYYY-MM-DD HH:MM' (24h)")}, ["text", "remind_at"]),
    _schema("list_reminders", "List pending reminders."),
    _schema("save_fact", "Save a lasting fact about the owner to long-term memory.",
            {"fact": _s("One short sentence")}, ["fact"]),
]


def run_tool(name: str, args) -> str:
    fn = TOOLS.get(name)
    if not fn:
        return f"Error: unknown tool '{name}'."
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return "Error: arguments were not valid JSON."
    allowed = inspect.signature(fn).parameters
    args = {k: v for k, v in (args or {}).items() if k in allowed}
    try:
        return fn(**args)
    except TypeError as e:
        return f"Error: bad arguments for {name}: {e}"
    except Exception as e:
        return f"Error while running {name}: {e}"


# ---------- Prompt ----------
def build_system_prompt() -> str:
    profile = PROFILE_PATH.read_text(encoding="utf-8") if PROFILE_PATH.exists() else ""
    with db() as c:
        facts = [r["fact"] for r in c.execute("SELECT fact FROM facts ORDER BY id")]
    facts_text = "\n".join(f"- {f}" for f in facts) or "(nothing yet)"
    now = datetime.now()

    return f"""You are Jarvis, a personal AI assistant for one person: your owner.
Be concise, warm and practical. Never invent facts about the owner.

Current date and time: {now.strftime('%A, %Y-%m-%d %H:%M')}

TOOL RULES:
- To add/list/complete tasks, log habits, set reminders or remember facts, you MUST call
  the matching tool. Never claim you did something without calling the tool.
- Convert relative times ("tomorrow 6pm", "in 2 hours", "next Monday") into exact
  'YYYY-MM-DD HH:MM' (24h) using the current date and time above.
- To complete a task, call list_tasks first if you don't know its id.
- If the owner shares a lasting personal fact or preference, call save_fact.
- After tools return, confirm briefly in plain language.

ABOUT THE OWNER (profile):
{profile}

THINGS YOU REMEMBER:
{facts_text}
"""


def load_history() -> list:
    with db() as c:
        rows = c.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (HISTORY_LIMIT,)
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ---------- Agent loop (streams events to the client) ----------
def ev(kind: str, **data) -> str:
    return json.dumps({"type": kind, **data}) + "\n"


def agent_stream(user_message: str, think: bool):
    messages = (
        [{"role": "system", "content": build_system_prompt()}]
        + load_history()
        + [{"role": "user", "content": user_message}]
    )
    final_text = ""
    finished = False

    try:
        for _ in range(MAX_STEPS):
            content_buf, tool_calls = "", []
            payload = {
                "model": MODEL,
                "messages": messages,
                "tools": TOOL_SCHEMAS,
                "stream": True,
                "think": think,
                "keep_alive": KEEP_ALIVE,
                "options": {"num_ctx": NUM_CTX},
            }
            with httpx.stream("POST", OLLAMA_URL, json=payload, timeout=None) as resp:
                if resp.status_code != 200:
                    resp.read()
                    yield ev("error", text=f"Ollama returned {resp.status_code}: {resp.text[:300]}")
                    return
                for line in resp.iter_lines():
                    if not line:
                        continue
                    msg = json.loads(line).get("message", {})
                    if msg.get("thinking"):
                        yield ev("thinking", text=msg["thinking"])
                    if msg.get("content"):
                        content_buf += msg["content"]
                        yield ev("content", text=msg["content"])
                    if msg.get("tool_calls"):
                        tool_calls.extend(msg["tool_calls"])

            if not tool_calls:
                final_text, finished = content_buf, True
                break

            messages.append({"role": "assistant", "content": content_buf, "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function", {})
                name, args = fn.get("name", ""), fn.get("arguments") or {}
                result = run_tool(name, args)
                yield ev("tool", name=name, args=args, result=result)
                messages.append({"role": "tool", "tool_name": name, "content": result})

        if not finished:
            yield ev("error", text="Stopped: too many tool steps in one message.")
    except httpx.HTTPError as e:
        yield ev("error", text=f"Could not reach Ollama. Is it running? ({e})")
        return

    final_text = re.sub(r"<think>.*?</think>", "", final_text, flags=re.DOTALL).strip()
    with db() as c:
        c.execute("INSERT INTO messages (role, content, ts) VALUES ('user', ?, ?)", (user_message, now_str()))
        if final_text:
            c.execute("INSERT INTO messages (role, content, ts) VALUES ('assistant', ?, ?)", (final_text, now_str()))
    yield ev("done")


# ---------- API ----------
class ChatIn(BaseModel):
    message: str
    think: bool = True


class FactIn(BaseModel):
    fact: str


@app.get("/")
def health():
    return {"status": "Jarvis is running", "model": MODEL}


@app.post("/chat")
def chat(body: ChatIn):
    return StreamingResponse(agent_stream(body.message, body.think), media_type="application/x-ndjson")


@app.get("/tasks")
def api_tasks():
    return {"text": list_tasks("open")}


@app.get("/habits")
def api_habits():
    return {"text": habit_stats()}


@app.get("/reminders/due")
def api_due_reminders():
    with db() as c:
        rows = c.execute(
            "SELECT id, text, remind_at FROM reminders WHERE fired = 0 AND remind_at <= ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M"),),
        ).fetchall()
        c.executemany("UPDATE reminders SET fired = 1 WHERE id = ?", [(r["id"],) for r in rows])
    return [dict(r) for r in rows]


@app.post("/facts")
def add_fact(body: FactIn):
    return {"text": save_fact(body.fact)}


@app.get("/facts")
def get_facts():
    with db() as c:
        rows = c.execute("SELECT id, fact FROM facts ORDER BY id").fetchall()
    return [dict(r) for r in rows]


@app.delete("/facts/{fact_id}")
def delete_fact(fact_id: int):
    with db() as c:
        c.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
    return {"ok": True}


@app.post("/reset")
def reset_history():
    with db() as c:
        c.execute("DELETE FROM messages")
    return {"ok": True}