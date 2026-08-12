#!/bin/bash
# ComfyUI 启动/重启脚本（幂等：已在运行会先停旧进程再启动）
# 前提：已按 README 第 3B 步克隆 ComfyUI 并放好 H3 模型文件
cd "$(dirname "$0")"
SCRIPT_DIR=$(pwd)
mkdir -p logs

COMFYUI_DIR=${COMFYUI_DIR:-../ComfyUI}   # ComfyUI 所在目录，可用环境变量覆盖
if [ ! -f "$COMFYUI_DIR/main.py" ]; then
  echo "找不到 ComfyUI：$COMFYUI_DIR"
  echo "用法：COMFYUI_DIR=/你的/ComfyUI路径 bash start_comfyui.sh"
  exit 1
fi

PORT=8188
LOG="$SCRIPT_DIR/logs/comfyui.log"
PIDFILE="$SCRIPT_DIR/logs/comfyui.pid"

# ---- 1. 停掉旧进程（如有）----
OLD_PID=""
[ -f "$PIDFILE" ] && OLD_PID=$(cat "$PIDFILE")
if [ -z "$OLD_PID" ] || ! kill -0 "$OLD_PID" 2>/dev/null; then
  # pid 文件失效时按端口和命令行特征找
  OLD_PID=$(ps aux | grep "[m]ain.py --listen 0.0.0.0 --port $PORT" | awk '{print $2}' | head -1)
fi

if [ -n "$OLD_PID" ]; then
  echo "发现旧进程 (pid $OLD_PID)，正在停止..."
  kill "$OLD_PID" 2>/dev/null
  for i in $(seq 1 10); do
    kill -0 "$OLD_PID" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "进程未退出，强制结束..."
    kill -9 "$OLD_PID" 2>/dev/null
    sleep 2
  fi
fi

# ---- 2. 确认端口已释放 ----
if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/system_stats"; then
  echo "错误：端口 $PORT 仍被占用（可能是其他程序），请手动检查"
  exit 1
fi

# ---- 3. 启动新进程 ----
cd "$COMFYUI_DIR"
CUDA_VISIBLE_DEVICES=2 nohup python3 main.py \
  --listen 0.0.0.0 --port $PORT --use-sage-attention \
  > "$LOG" 2>&1 &
NEW_PID=$!
echo $NEW_PID > "$PIDFILE"
echo "ComfyUI 启动中 (pid $NEW_PID)，端口 $PORT"
echo "日志：$LOG"

# ---- 4. 等待就绪（最多 120 秒）----
echo -n "等待服务就绪"
for i in $(seq 1 60); do
  if ! kill -0 "$NEW_PID" 2>/dev/null; then
    echo ""
    echo "启动失败，进程已退出，最近日志："
    tail -20 "$LOG"
    exit 1
  fi
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/system_stats"; then
    echo ""
    echo "ComfyUI 已就绪 (pid $NEW_PID)"
    exit 0
  fi
  echo -n "."
  sleep 2
done
echo ""
echo "等待超时（120s），服务未就绪，请查看日志：tail -50 $LOG"
exit 1
