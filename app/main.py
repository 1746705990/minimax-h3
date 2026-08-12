"""Web 入口：页面 + API。

启动：python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import os
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, moderation
from .auth import SESSION_COOKIE, check_invite, current_user, hash_password, verify_password
from .config import CFG, ROOT

app = FastAPI(title=CFG["site_name"], docs_url=None, redoc_url=None)
db.init_db()

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/{filename}.txt")
def verify_txt(filename: str):
    """站点验证文件：仅放行项目根目录下 32 位 hex 命名的 txt"""
    if not all(c in "0123456789abcdef" for c in filename) or len(filename) != 32:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    path = os.path.join(ROOT, filename + ".txt")
    if not os.path.isfile(path):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return FileResponse(path, media_type="text/plain")


# ---------- 认证 ----------

class AuthIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=6, max_length=64)
    invite_code: str = ""


@app.post("/api/register")
def register(body: AuthIn):
    if not body.username.replace("_", "").isalnum():
        raise HTTPException(400, "用户名只能包含字母、数字、下划线")
    if not check_invite(body.invite_code):
        raise HTTPException(403, "邀请码不正确")
    uid, is_admin = db.create_user(body.username, hash_password(body.password))
    if uid is None:
        raise HTTPException(409, "用户名已被注册")
    token = db.create_session(uid)
    resp = JSONResponse({"ok": True, "username": body.username, "is_admin": bool(is_admin)})
    resp.set_cookie(SESSION_COOKIE, token, max_age=7 * 86400, httponly=True, samesite="lax")
    return resp


@app.post("/api/login")
def login(body: AuthIn):
    user = db.get_user(body.username)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    token = db.create_session(user["id"])
    resp = JSONResponse({"ok": True, "username": user["username"],
                         "is_admin": bool(user["is_admin"])})
    resp.set_cookie(SESSION_COOKIE, token, max_age=7 * 86400, httponly=True, samesite="lax")
    return resp


@app.post("/api/logout")
def logout(request: Request):
    db.delete_session(request.cookies.get(SESSION_COOKIE, ""))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"username": user["username"], "is_admin": bool(user["is_admin"]),
            "quota_per_day": CFG["quota_per_day"],
            "used_today": db.user_jobs_today(user["id"]),
            "site_name": CFG["site_name"],
            "default_size": CFG["default_size"],
            "default_seconds": CFG["default_seconds"]}


# ---------- 任务 ----------

class JobIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=CFG.get("max_prompt_length", 4000))
    size: str = "864x480"
    seconds: int = Field(default=5, ge=1, le=15)
    engine: str = "turbo"       # turbo=极速(Turbo LoRA 8步) / standard=品质(标准20步)


ALLOWED_SIZES = {"864x480", "1280x736"}
ALLOWED_ENGINES = {"turbo", "standard"}


@app.post("/api/jobs")
def create_job(body: JobIn, user=Depends(current_user)):
    if body.size not in ALLOWED_SIZES:
        raise HTTPException(400, "不支持的分辨率")
    if body.engine not in ALLOWED_ENGINES:
        raise HTTPException(400, "不支持的引擎")
    ok, reason = moderation.check_prompt(body.prompt)
    if not ok:
        raise HTTPException(400, reason)
    if CFG["quota_per_day"] > 0 and db.user_jobs_today(user["id"]) >= CFG["quota_per_day"]:
        raise HTTPException(429, f"今日额度已用完（每天 {CFG['quota_per_day']} 次）")
    if db.pending_count() >= CFG["max_pending_jobs"]:
        raise HTTPException(503, "当前排队人数过多，请稍后再试")
    jid = db.create_job(user, body.prompt.strip(), body.size, body.seconds, body.engine)
    return {"job_id": jid, "queue_position": db.queue_position(jid)}


def _job_dict(row, user):
    d = {
        "id": row["id"], "prompt": row["prompt"], "size": row["size"],
        "seconds": row["seconds"], "status": row["status"],
        "engine": row["engine"] if "engine" in row.keys() else "turbo",
        "backend": row["backend"], "error": row["error"],
        "created_at": row["created_at"], "finished_at": row["finished_at"],
    }
    if row["status"] == "completed" and row["video_file"]:
        d["video_url"] = f"/videos/{row['id']}.mp4"
    if row["status"] == "pending":
        d["queue_position"] = db.queue_position(row["id"])
    if row["status"] == "running" and row["started_at"]:
        d["elapsed"] = int(time.time() - row["started_at"])
    if user["is_admin"]:
        d["username"] = row["username"]
    return d


@app.get("/api/jobs")
def list_jobs(all: int = 0, user=Depends(current_user)):
    rows = db.list_jobs(user, all_jobs=bool(all))
    return {"jobs": [_job_dict(r, user) for r in rows],
            "pending_total": db.pending_count()}


@app.get("/api/admin/users")
def admin_users(user=Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(403, "仅管理员可查看")
    rows = db.list_users_with_stats()
    return {"users": [{
        "id": r["id"], "username": r["username"], "is_admin": bool(r["is_admin"]),
        "created_at": r["created_at"], "jobs_total": r["jobs_total"],
        "jobs_done": r["jobs_done"],
    } for r in rows]}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str, user=Depends(current_user)):
    row = db.get_job(job_id)
    if not row or (row["user_id"] != user["id"] and not user["is_admin"]):
        raise HTTPException(404, "任务不存在")
    return _job_dict(row, user)


@app.get("/videos/{filename}")
def get_video(filename: str, request: Request, user=Depends(current_user)):
    if not filename.endswith(".mp4") or "/" in filename:
        raise HTTPException(404)
    job_id = filename[:-4]
    row = db.get_job(job_id)
    if not row or (row["user_id"] != user["id"] and not user["is_admin"]):
        raise HTTPException(404, "视频不存在")
    path = os.path.join(CFG["output_dir"], filename)
    if not os.path.exists(path):
        raise HTTPException(404, "视频已过期清理")

    file_size = os.path.getsize(path)
    range_header = request.headers.get("range")
    if range_header:
        # 支持 HTTP Range：浏览器拖动进度条/完整播放必需
        try:
            _, rng = range_header.split("=", 1)
            start_s, _, end_s = rng.partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
            end = min(end, file_size - 1)
            if start > end:
                raise ValueError
        except ValueError:
            raise HTTPException(416, "Range Not Satisfiable")
        length = end - start + 1

        def iterfile():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(iterfile(), status_code=206, media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
                "Content-Disposition": f'inline; filename="{filename}"',
            })
    return FileResponse(path, media_type="video/mp4",
                        headers={"Accept-Ranges": "bytes",
                                 "Content-Disposition": f'inline; filename="{filename}"'})
