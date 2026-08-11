#!/bin/bash
# 路线二：启动 ComfyUI（兜底方案，torch cu124 与驱动 535 兼容）
# 前提：已按 README 第 3B 步克隆 ComfyUI 并放好 H3 模型文件
cd "$(dirname "$0")"
mkdir -p logs

COMFYUI_DIR=${COMFYUI_DIR:-../ComfyUI}   # ComfyUI 所在目录，可用环境变量覆盖
if [ ! -f "$COMFYUI_DIR/main.py" ]; then
  echo "找不到 ComfyUI：$COMFYUI_DIR"
  echo "用法：COMFYUI_DIR=/你的/ComfyUI路径 bash start_comfyui.sh"
  exit 1
fi

cd "$COMFYUI_DIR"
CUDA_VISIBLE_DEVICES=2 nohup python3 main.py \
  --listen 0.0.0.0 --port 8188 --use-sage-attention \
  > "$OLDPWD/logs/comfyui.log" 2>&1 &
echo $! > "$OLDPWD/logs/comfyui.pid"
echo "ComfyUI 启动中 (pid $(cat $OLDPWD/logs/comfyui.pid))，端口 8188"
echo "就绪验证：curl http://127.0.0.1:8188/system_stats"
