import re
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np

BASE = Path(__file__).parent
DB_PATH = BASE / "jarvis.db"

EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"
CHUNK_CHARS = 700
CHUNK_OVERLAP = 100


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init():
    with _db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB NOT NULL,
                created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL);
            """
        )


_init()


# ---------- Embeddings ----------
def embed(texts: list, kind: str = "document") -> np.ndarray:
    prefix = "search_document: " if kind == "document" else "search_query: "
    out = []
    for i in range(0, len(texts), 16):
        batch = [prefix + t for t in texts[i : i + 16]]
        r = httpx.post(
            EMBED_URL,
            json={"model": EMBED_MODEL, "input": batch, "keep_alive": "30m"},
            timeout=120,
        )
        r.raise_for_status()
        out.extend(r.json()["embeddings"])
    arr = np.array(out, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return arr / norms


# ---------- Chunking ----------
def chunk_text(text: str) -> list:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > CHUNK_CHARS:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:CHUNK_CHARS])
            p = p[CHUNK_CHARS - CHUNK_OVERLAP :]
        if len(cur) + len(p) + 2 <= CHUNK_CHARS:
            cur = f"{cur}\n\n{p}" if cur else p
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


# ---------- Storing ----------
def add_chunks(source: str, texts: list) -> int:
    if not texts:
        return 0
    vecs = embed(texts, "document")
    now = datetime.now().isoformat(timespec="seconds")
    with _db() as c:
        c.executemany(
            "INSERT INTO chunks (source, text, embedding, created) VALUES (?, ?, ?, ?)",
            [(source, t, v.tobytes(), now) for t, v in zip(texts, vecs)],
        )
    return len(texts)


def remove_source(source: str) -> None:
    with _db() as c:
        c.execute("DELETE FROM chunks WHERE source = ?", (source,))


def get_mtime(path: str):
    with _db() as c:
        row = c.execute("SELECT mtime FROM sources WHERE path = ?", (path,)).fetchone()
    return row["mtime"] if row else None


def set_mtime(path: str, mtime: float) -> None:
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO sources (path, mtime) VALUES (?, ?)", (path, mtime))


# ---------- Searching ----------
_cache = {"key": None, "ids": None, "matrix": None}


def _load() -> None:
    with _db() as c:
        key = tuple(c.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM chunks").fetchone())
        if key == _cache["key"]:
            return
        rows = c.execute("SELECT id, embedding FROM chunks").fetchall()
    if rows:
        _cache["ids"] = np.array([r["id"] for r in rows])
        _cache["matrix"] = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    else:
        _cache["ids"], _cache["matrix"] = None, None
    _cache["key"] = key


def _label(source: str) -> str:
    if source.startswith("chat:"):
        return "chat " + source[5:]
    return Path(source).name


def search(query: str, top_k: int = 3, min_score: float = 0.6) -> list:
    _load()
    if _cache["matrix"] is None:
        return []
    q = embed([query], "query")[0]
    scores = _cache["matrix"] @ q
    order = np.argsort(-scores)[:top_k]
    hits = [(int(_cache["ids"][i]), float(scores[i])) for i in order if scores[i] >= min_score]
    results = []
    with _db() as c:
        for cid, score in hits:
            row = c.execute("SELECT source, text FROM chunks WHERE id = ?", (cid,)).fetchone()
            if row:
                results.append(
                    {"label": _label(row["source"]), "text": row["text"], "score": score}
                )
    return results


def stats() -> dict:
    with _db() as c:
        total = c.execute("SELECT COUNT(*) n FROM chunks").fetchone()["n"]
        chats = c.execute("SELECT COUNT(*) n FROM chunks WHERE source LIKE 'chat:%'").fetchone()["n"]
        files = c.execute("SELECT COUNT(*) n FROM sources").fetchone()["n"]
    return {"total_chunks": total, "from_chats": chats, "from_files": total - chats, "files_ingested": files}