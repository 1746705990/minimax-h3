# H3 视频生成站 · 部署文档

在浪潮 AIStation 容器（Ubuntu 22.04 / 驱动 535 / CUDA 12.4 / H20-3e）上本地部署
MiniMax-H3 视频生成，并提供一个公开访问的网页：用户注册登录 → 提交提示词 →
排队生成 → 在线播放/下载视频。

## 架构

```
外部用户
   │  frp（你已熟悉，指向容器 8000 端口）
   ▼
FastAPI Web 服务 (8000)  ←→  SQLite（用户/会话/任务队列）
   │                            ▲
   │ 静态页面 + API             │ 认领任务
   ▼                            │
队列 worker（独立进程）──────────┘
   │ 自动二选一（config.yaml: backend: auto）
   ├─→ sglang serve (30010)   路线一：性能优先，新版 CUDA 工具链，不保证兼容驱动 535
   └─→ ComfyUI (8188)         路线二：兜底，torch cu124 与驱动 535 确认兼容
```

生成引擎（sglang 或 ComfyUI）与网站服务相互独立，网站代码不用改，
worker 每个任务执行前自动探测哪个引擎活着就用哪个。

## 第 1 步：准备 Python 环境

```bash
# 必须用 Python ≥ 3.10（别用之前那个 pyenv 的 3.7）
python3 --version

cd h3video
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 第 2 步：修改配置

```bash
vi config.yaml
# 必改：secret_key 换成随机字符串
# 建议：invite_code 设置一个邀请码（公开服务防滥用）
#       quota_per_day 按需调整
```

## 第 3 步：启动生成引擎（二选一，失败自动回退）

### 路线一（先试）：sglang diffusion

```bash
pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple
uv pip install "sglang[diffusion]" --prerelease=allow

bash start_sglang.sh        # 先在脚本里确认 CUDA_VISIBLE_DEVICES 是空闲卡
# 等待 10-30 分钟模型加载，验证：
curl http://127.0.0.1:30010/models
```

能返回模型信息 → 路线一可用，直接跳到第 4 步。
报 CUDA 驱动相关错误 → 放弃，走路线二。

### 路线二（兜底）：ComfyUI

```bash
# 1) torch 用 cu124（与驱动 535 兼容）
pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 \
  --index-url https://download.pytorch.org/whl/cu124

# 2) 克隆 ComfyUI（与 h3video 同级目录）
cd ..
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3) 下载 MiniMax-H3 模型（预留 150GB+ 磁盘！）
#    推荐用魔搭，国内速度快：
pip install modelscope -i https://pypi.tuna.tsinghua.edu.cn/simple
#    按 ComfyUI 官方 H3 页面说明，把 transformer / 文本编码器 / VAE
#    分别放入 models/diffusion_models、models/text_encoders、models/vae

# 4) 启动
cd ../h3video
bash start_comfyui.sh
curl http://127.0.0.1:8188/system_stats     # 验证

# 5) 工作流（已内置两份，开箱即用）
#    workflow_turbo.json    极速引擎：bf16 + Turbo LoRA(ema_V4, merge) + 8 步
#    workflow_standard.json 品质引擎：bf16 + 无 LoRA + res_multistep 20 步
#    config.yaml 的 comfyui.engines 里已按这两份工作流预填好全部节点 ID。
#    若你在 ComfyUI 里改过画布重新导出，需核对 engines 下各节点 ID。
```

## 引擎与档位

| 引擎 | 技术组合 | 5s/0.9MP 参考耗时 | 适用 |
|---|---|---|---|
| ⚡ turbo（默认）| bf16 + Turbo LoRA 8步 strength 0.85 | ~6~8 分钟 | 日常生成、草稿 |
| 💎 standard | bf16 + 标准 20 步 | ~45 分钟 | 终稿、最高画质 |

时长 5/10/15 秒均可（15 秒约 3 倍耗时；bf16 权重下 15 秒有声已验证稳定）。
分辨率 864×480(0.4MP) / 1280×736(0.9MP)。失败自动换种子重试 1 次。

## 第 4 步：启动网站服务

```bash
bash start.sh
# 验证：
curl http://127.0.0.1:8000/          # 返回 HTML
tail -f logs/worker.log              # 观察队列消费
```

## 第 5 步：frp 暴露（和你宝塔面板一样的做法）

frpc.toml 里加一条，指向容器 8000 端口：

```toml
[[proxies]]
name = "h3video"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8000
remotePort = 你的公网端口
```

重启 frpc，外部访问 `http://你的frp服务器:端口` 即可打开页面。
**第一个注册的用户自动成为管理员**（可看到任务所属用户名）。

## 日常运维

| 操作 | 命令 |
|---|---|
| 看网站日志 | `tail -f logs/web.log` |
| 看队列日志 | `tail -f logs/worker.log` |
| 停止网站 | `bash stop.sh` |
| 改敏感词后热加载 | 重启 web：`bash stop.sh && bash start.sh` |
| 视频清理 | worker 每小时自动清理超过 `video_retention_days` 天的视频 |

## 合规提醒（公开服务必读）

1. H3 生成内容带 AI 水印，页面已提示用户勿用于违规用途；
2. `banned_words.txt` 务必认真维护，建议接入专业内容安全 API
   （在 `app/moderation.py` 里有接入点）；
3. 公网提供服务注意备案与平台（AIStation）使用规定；
4. H3 社区许可禁止在美国/欧盟/英国/韩国提供服务，国内自用没问题。

## 故障排查

| 现象 | 排查 |
|---|---|
| 任务一直排队 | `tail logs/worker.log`；大概率是两个引擎都没起，或都不通 |
| 任务立刻失败，提示后端不可达 | `curl http://127.0.0.1:30010/models` 和 `curl http://127.0.0.1:8188/system_stats` 哪个通 |
| sglang 起不来 | `tail logs/sglang.log`，CUDA 错误→换路线二 |
| ComfyUI 任务失败"找不到提示词节点" | 工作流 JSON 里的节点 ID 与 engines 配置不一致 |
| 任务失败但第二次尝试成功 | 正常：worker 会自动换种子重试 1 次（规避偶发 NaN）|
| 品质档(standard)超时 | comfyui.engines.standard.timeout_minutes 调大（15s/20步 可能 3 小时+）|
| 共享节点显卡被别人占了 | 启动脚本里的 CUDA_VISIBLE_DEVICES 换成当前空闲卡 |
