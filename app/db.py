"""SQLite 数据层：用户、会话、任务。WAL 模式支持 web/worker 双进程并发。"""
import sqlite3
import threading
import time
import uuid

from .config import CFG

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    prompt TEXT NOT NULL,
    size TEXT NOT NULL,
    seconds INTEGER NOT NULL,
    engine TEXT NOT NULL DEFAULT 'turbo',  -- turbo/standard
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/running/completed/failed
    backend TEXT DEFAULT '',
    video_file TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at REAL NOT NULL,
    started_at REAL DEFAULT 0,
    finished_at REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id, created_at);
"""


def conn():
    if not hasattr(_local, "c") or _local.c is None:
        _local.c = sqlite3.connect(CFG["database"], timeout=30)
        _local.c.row_factory = sqlite3.Row
        _local.c.execute("PRAGMA journal_mode=WAL")
    return _local.c


def init_db():
    conn().executescript(SCHEMA)
    # 老库迁移：补 engine 列（turbo/standard）
    cols = [r["name"] for r in conn().execute("PRAGMA table_info(jobs)")]
    if "engine" not in cols:
        conn().execute("ALTER TABLE jobs ADD COLUMN engine TEXT NOT NULL DEFAULT 'turbo'")
    conn().commit()


# ---------- 用户与会话 ----------

def create_user(username, password_hash):
    c = conn()
    is_admin = 1 if c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] == 0 else 0
    try:
        cur = c.execute(
            "INSERT INTO users(username, password_hash, is_admin, created_at) VALUES (?,?,?,?)",
            (username, password_hash, is_admin, time.time()))
        c.commit()
        return cur.lastrowid, is_admin
    except sqlite3.IntegrityError:
        return None, 0


def get_user(username):
    return conn().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def get_user_by_id(uid):
    return conn().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def create_session(user_id, days=7):
    token = uuid.uuid4().hex + uuid.uuid4().hex
    conn().execute("INSERT INTO sessions VALUES (?,?,?)",
                   (token, user_id, time.time() + days * 86400))
    conn().commit()
    return token


def get_session_user(token):
    if not token:
        return None
    row = conn().execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    if not row or row["expires_at"] < time.time():
        return None
    return get_user_by_id(row["user_id"])


def delete_session(token):
    conn().execute("DELETE FROM sessions WHERE token=?", (token,))
    conn().commit()


def list_users_with_stats():
    """管理员用：全部用户及其任务统计"""
    return conn().execute(
        "SELECT u.id, u.username, u.is_admin, u.created_at, "
        "COUNT(j.id) AS jobs_total, "
        "COALESCE(SUM(CASE WHEN j.status='completed' THEN 1 ELSE 0 END), 0) AS jobs_done "
        "FROM users u LEFT JOIN jobs j ON j.user_id = u.id "
        "GROUP BY u.id ORDER BY u.id").fetchall()


# ---------- 任务 ----------

def new_job_id():
    return time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:10]


def create_job(user, prompt, size, seconds, engine="turbo"):
    jid = new_job_id()
    conn().execute(
        "INSERT INTO jobs(id,user_id,username,prompt,size,seconds,engine,status,created_at) "
        "VALUES (?,?,?,?,?,?,?,'pending',?)",
        (jid, user["id"], user["username"], prompt, size, seconds, engine, time.time()))
    conn().commit()
    return jid


def user_jobs_today(user_id):
    day_start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    return conn().execute(
        "SELECT COUNT(*) n FROM jobs WHERE user_id=? AND created_at>=?",
        (user_id, day_start)).fetchone()["n"]


def pending_count():
    return conn().execute("SELECT COUNT(*) n FROM jobs WHERE status='pending'").fetchone()["n"]


def queue_position(job_id):
    row = conn().execute("SELECT created_at FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return 0
    return conn().execute(
        "SELECT COUNT(*) n FROM jobs WHERE status='pending' AND created_at<?",
        (row["created_at"],)).fetchone()["n"] + 1


def list_jobs(user, limit=50, all_jobs=False):
    if all_jobs and user["is_admin"]:
        return conn().execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return conn().execute(
        "SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user["id"], limit)).fetchall()


def get_job(job_id):
    return conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def claim_next_job():
    """原子认领最老的 pending 任务，返回行或 None"""
    c = conn()
    row = c.execute(
        "SELECT id FROM jobs WHERE status='pending' ORDER BY created_at LIMIT 1").fetchone()
    if not row:
        return None
    cur = c.execute(
        "UPDATE jobs SET status='running', started_at=? WHERE id=? AND status='pending'",
        (time.time(), row["id"]))
    c.commit()
    if cur.rowcount == 0:
        return None
    return get_job(row["id"])


def finish_job(job_id, ok, backend="", video_file="", error=""):
    conn().execute(
        "UPDATE jobs SET status=?, backend=?, video_file=?, error=?, finished_at=? WHERE id=?",
        ("completed" if ok else "failed", backend, video_file, error, time.time(), job_id))
    conn().commit()


def recover_running():
    """启动时把上次遗留的 running 任务放回队列"""
    conn().execute("UPDATE jobs SET status='pending', started_at=0 WHERE status='running'")
    conn().commit()


def cleanup_old_videos(output_dir, retention_days):
    cut = time.time() - retention_days * 86400
    rows = conn().execute(
        "SELECT id, video_file FROM jobs WHERE video_file!='' AND finished_at<?",
        (cut,)).fetchall()
    import os
    n = 0
    for r in rows:
        p = os.path.join(output_dir, r["video_file"])
        if os.path.exists(p):
            try:
                os.remove(p)
                n += 1
            except OSError:
                pass
    conn().execute(
        "UPDATE jobs SET video_file='' WHERE video_file!='' AND finished_at<?", (cut,))
    conn().commit()
    return n
