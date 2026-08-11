"""生成后端：SGLang Diffusion 优先，ComfyUI 兜底。

两个后端统一实现：
    available() -> bool
    generate(prompt, size, seconds, out_path) -> None   # 失败抛异常
"""
import copy
import json
import logging
import random
import time

import requests

log = logging.getLogger("backends")


class SGLangBackend:
    """sglang serve 的 OpenAI 风格异步视频 API：
    POST /v1/videos 创建 -> GET /v1/videos/{id} 轮询 -> GET .../content 下载
    """
    name = "sglang"

    def __init__(self, cfg):
        self.base = cfg["base_url"].rstrip("/")
        self.key = cfg.get("api_key") or "EMPTY"
        self.timeout = cfg.get("timeout_minutes", 90) * 60

    def _headers(self):
        return {"Authorization": f"Bearer {self.key}"}

    def available(self):
        try:
            r = requests.get(f"{self.base}/models", headers=self._headers(), timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def generate(self, prompt, size, seconds, out_path):
        r = requests.post(
            f"{self.base}/v1/videos",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"prompt": prompt, "size": size, "seconds": seconds},
            timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"sglang 创建任务失败: {r.status_code} {r.text[:300]}")
        vid = r.json().get("id")
        if not vid:
            raise RuntimeError(f"sglang 未返回 video id: {r.text[:300]}")

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(5)
            s = requests.get(f"{self.base}/v1/videos/{vid}",
                             headers=self._headers(), timeout=30)
            if s.status_code != 200:
                continue
            status = s.json().get("status", "")
            if status == "completed":
                break
            if status in ("failed", "cancelled"):
                raise RuntimeError(f"sglang 任务失败: {s.text[:300]}")
        else:
            raise TimeoutError("sglang 生成超时")

        d = requests.get(f"{self.base}/v1/videos/{vid}/content",
                         headers=self._headers(), timeout=600, stream=True)
        if d.status_code != 200:
            raise RuntimeError(f"sglang 下载视频失败: {d.status_code}")
        with open(out_path, "wb") as f:
            for chunk in d.iter_content(1 << 20):
                f.write(chunk)


class ComfyUIBackend:
    """ComfyUI API：POST /prompt 提交工作流 -> GET /history/{id} 轮询
    -> GET /view 下载产物。支持多引擎（turbo/standard），每个引擎对应
    一份从 ComfyUI 界面导出的 API 格式工作流 + 各自的节点 ID 映射。
    """

    @property
    def name(self):
        return f"comfyui-{self.engine}"

    def __init__(self, cfg, engine="turbo"):
        self.base = cfg["base_url"].rstrip("/")
        self.engine = engine
        engines = cfg.get("engines") or {}
        if engine not in engines:
            raise RuntimeError(f"config.yaml 的 comfyui.engines 里没有引擎「{engine}」")
        prof = engines[engine]
        self.workflow_file = prof["workflow_file"]
        self.prompt_node = str(prof.get("prompt_node_id") or "")
        self.prompt_keys = prof.get("prompt_keys") or ["text", "prompt"]
        self.seed_node = str(prof.get("seed_node_id") or "")
        self.duration_node = str(prof.get("duration_node_id") or "")
        self.resolution_node = str(prof.get("resolution_node_id") or "")
        self.steps_node = str(prof.get("steps_node_id") or "")
        self.lora_node = str(prof.get("lora_node_id") or "")
        self.steps = prof.get("steps")            # None = 用工作流默认值
        self.strength = prof.get("strength")      # None = 用工作流默认值
        self.size_map = cfg.get("size_to_megapixels") or {}
        self.timeout = prof.get("timeout_minutes", 90) * 60
        self._wf = None

    def available(self):
        try:
            r = requests.get(f"{self.base}/system_stats", timeout=5)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def _load_workflow(self):
        if self._wf is None:
            with open(self.workflow_file, "r", encoding="utf-8") as f:
                self._wf = json.load(f)
        return copy.deepcopy(self._wf)

    def generate(self, prompt, size, seconds, out_path):
        wf = self._load_workflow()
        # 提示词：按配置的字段名候选逐个尝试
        if self.prompt_node and self.prompt_node in wf:
            inputs = wf[self.prompt_node]["inputs"]
            for key in self.prompt_keys:
                if key in inputs:
                    inputs[key] = prompt
                    break
            else:
                raise RuntimeError(
                    f"节点 {self.prompt_node} 里没有可写的提示词字段（现有: {list(inputs)}）")
        else:
            raise RuntimeError(
                f"工作流中找不到提示词节点 {self.prompt_node}，请检查 config.yaml 的 prompt_node_id")
        # 随机种子
        if self.seed_node and self.seed_node in wf:
            for key in ("seed", "noise_seed"):
                if key in wf[self.seed_node].get("inputs", {}):
                    wf[self.seed_node]["inputs"][key] = random.randint(0, 2**63 - 1)
        # 时长（秒）
        if self.duration_node and self.duration_node in wf:
            d_inputs = wf[self.duration_node]["inputs"]
            for key in ("value", "seconds", "duration"):
                if key in d_inputs:
                    d_inputs[key] = seconds
                    break
        # 分辨率：按 size 映射到 megapixels
        if self.resolution_node and self.resolution_node in wf and size in self.size_map:
            wf[self.resolution_node]["inputs"]["megapixels"] = self.size_map[size]
        # 步数 / LoRA 强度覆写（配置了才改，否则用工作流默认值）
        if self.steps is not None and self.steps_node and self.steps_node in wf:
            wf[self.steps_node]["inputs"]["steps"] = int(self.steps)
        if self.strength is not None and self.lora_node and self.lora_node in wf:
            wf[self.lora_node]["inputs"]["strength"] = float(self.strength)

        r = requests.post(f"{self.base}/prompt", json={"prompt": wf}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"ComfyUI 提交失败: {r.status_code} {r.text[:300]}")
        pid = r.json().get("prompt_id")
        if not pid:
            raise RuntimeError(f"ComfyUI 未返回 prompt_id: {r.text[:300]}")

        deadline = time.time() + self.timeout
        entry = None
        while time.time() < deadline:
            time.sleep(5)
            h = requests.get(f"{self.base}/history/{pid}", timeout=30)
            if h.status_code != 200:
                continue
            data = h.json()
            if pid not in data:
                continue
            entry = data[pid]
            status = entry.get("status", {})
            if status.get("completed") or entry.get("outputs"):
                break
            if status.get("status_str") == "error":
                msgs = status.get("messages", [])
                raise RuntimeError(f"ComfyUI 执行出错: {json.dumps(msgs)[:300]}")
        else:
            raise TimeoutError("ComfyUI 生成超时")

        # 在输出里找视频文件（兼容 VHS_VideoCombine 的 gifs 与核心 SaveVideo 的 videos）
        found = None
        for node_out in (entry.get("outputs") or {}).values():
            for key in ("videos", "gifs", "images"):
                for item in node_out.get(key, []) or []:
                    fmt = str(item.get("format", ""))
                    fname = item.get("filename", "")
                    if "video" in fmt or fname.lower().endswith((".mp4", ".webm", ".mov")):
                        found = item
                        break
                if found:
                    break
            if found:
                break
        if not found:
            raise RuntimeError("ComfyUI 输出中没有视频文件，请检查工作流是否含视频保存节点")

        d = requests.get(f"{self.base}/view", params={
            "filename": found["filename"],
            "subfolder": found.get("subfolder", ""),
            "type": found.get("type", "output"),
        }, timeout=600)
        if d.status_code != 200:
            raise RuntimeError(f"ComfyUI 下载视频失败: {d.status_code}")
        with open(out_path, "wb") as f:
            f.write(d.content)


_BACKENDS = {"sglang": SGLangBackend, "comfyui": ComfyUIBackend}


def pick_backend(cfg, engine="turbo"):
    """按配置选择可用后端；auto 模式优先 sglang，失败回退 comfyui。
    engine（turbo/standard）只对 comfyui 后端有意义。"""
    mode = cfg.get("backend", "auto")
    order = ["sglang", "comfyui"] if mode == "auto" else [mode]
    last_err = None
    for name in order:
        try:
            be = _BACKENDS[name](cfg[name]) if name == "sglang" \
                else _BACKENDS[name](cfg[name], engine)
            if be.available():
                log.info("使用后端: %s (engine=%s)", name, engine)
                return be
            last_err = f"{name} 服务不可达"
        except Exception as e:  # 配置错误等
            last_err = f"{name} 初始化失败: {e}"
    raise RuntimeError(f"没有可用的生成后端（{last_err}）。"
                       "请确认 sglang serve 或 ComfyUI 已启动。")
