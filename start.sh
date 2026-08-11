#!/bin/bash
# 启动 Web 服务 + 队列 worker（生成引擎 sglang/ComfyUI 需单独先启动，见 README）
cd "$(dirname "$0")"
mkdir -p data/outputs logs

if [ -f logs/web.pid ] && kill -0 "$(cat logs/web.pid)" 2>/dev/null; then
  echo "web 已在运行 (pid $(cat logs/web.pid))"
else
  nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    > logs/web.log 2>&1 &
  echo $! > logs/web.pid
  echo "web 已启动 (pid $(cat logs/web.pid))，端口 8000"
fi

if [ -f logs/worker.pid ] && kill -0 "$(cat logs/worker.pid)" 2>/dev/null; then
  echo "worker 已在运行 (pid $(cat logs/worker.pid))"
else
  nohup python3 -m app.worker > logs/worker.log 2>&1 &
  echo $! > logs/worker.pid
  echo "worker 已启动 (pid $(cat logs/worker.pid))"
fi
