#!/bin/bash
# 路线一：启动 sglang diffusion 服务（先试这条，见 README 第 3 步）
# 注意：sglang[diffusion] 需要新版 CUDA 工具链，在驱动 535 上不保证能跑，
#      失败就换 start_comfyui.sh 路线。
cd "$(dirname "$0")"
mkdir -p logs
export HF_ENDPOINT=https://hf-mirror.com   # 国内 HF 镜像

# CUDA_VISIBLE_DEVICES 指定空闲显卡（启动前先 nvidia-smi 确认！）
CUDA_VISIBLE_DEVICES=2 nohup sglang serve \
  --model-path MiniMaxAI/MiniMax-H3 \
  --num-gpus 1 \
  --dit-cpu-offload \
  --text-encoder-cpu-offload \
  --vae-cpu-offload \
  --pin-cpu-memory \
  --port 30010 \
  > logs/sglang.log 2>&1 &
echo $! > logs/sglang.pid
echo "sglang 启动中 (pid $(cat logs/sglang.pid))，模型加载需要 10-30 分钟"
echo "用 tail -f logs/sglang.log 观察，看到服务监听 30010 即就绪"
echo "就绪验证：curl http://127.0.0.1:30010/models"
