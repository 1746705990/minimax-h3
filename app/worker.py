"""队列 worker：独立进程运行，串行消费任务（单 GPU 一次只能生成一个视频）。

启动：python3 -m app.worker
"""
import logging
import os
import time

from . import db
from .backends import pick_backend
from .config import CFG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s %(message)s")
log = logging.getLogger("worker")


def process(job):
    out_name = f"{job['id']}.mp4"
    out_path = os.path.join(CFG["output_dir"], out_name)
    engine = job["engine"] if "engine" in job.keys() else "turbo"
    backend = pick_backend(CFG, engine)   # 每个任务重新探测，实现自动回退
    log.info("任务 %s 开始，后端=%s，提示词=%.40s...", job["id"], backend.name, job["prompt"])
    t0 = time.time()
    last_err = None
    # 失败自动重试一次（后端每次都会换新种子，可规避 NaN 类随机失败）
    for attempt in (1, 2):
        try:
            backend.generate(job["prompt"], job["size"], job["seconds"], out_path)
            last_err = None
            break
        except Exception as e:
            last_err = e
            log.warning("任务 %s 第 %d 次尝试失败: %.200s", job["id"], attempt, e)
    if last_err is not None:
        raise last_err
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError("后端未产出有效视频文件")
    log.info("任务 %s 完成，耗时 %.1fs", job["id"], time.time() - t0)
    return backend.name, out_name


def main():
    db.init_db()
    db.recover_running()
    log.info("worker 启动，backend=%s，输出目录=%s", CFG["backend"], CFG["output_dir"])
    last_cleanup = 0
    while True:
        try:
            job = db.claim_next_job()
            if job is None:
                if time.time() - last_cleanup > 3600:
                    n = db.cleanup_old_videos(CFG["output_dir"], CFG["video_retention_days"])
                    if n:
                        log.info("清理过期视频 %d 个", n)
                    last_cleanup = time.time()
                time.sleep(CFG["poll_interval_seconds"])
                continue
            try:
                backend_name, out_name = process(job)
                db.finish_job(job["id"], True, backend_name, out_name)
            except Exception as e:
                log.exception("任务 %s 失败", job["id"])
                db.finish_job(job["id"], False, error=str(e)[:500])
        except Exception:
            log.exception("worker 主循环异常，5s 后继续")
            time.sleep(5)


if __name__ == "__main__":
    main()
