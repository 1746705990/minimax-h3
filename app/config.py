"""配置加载"""
import os
import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _abs(p):
    return p if os.path.isabs(p) else os.path.join(_ROOT, p)


def load_config():
    path = os.environ.get("H3VIDEO_CONFIG", os.path.join(_ROOT, "config.yaml"))
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["database"] = _abs(cfg["database"])
    cfg["output_dir"] = _abs(cfg["output_dir"])
    comfy = cfg["comfyui"]
    if "engines" in comfy:                      # 双引擎结构
        for prof in comfy["engines"].values():
            prof["workflow_file"] = _abs(prof["workflow_file"])
    elif "workflow_file" in comfy:              # 兼容旧单工作流结构
        comfy["workflow_file"] = _abs(comfy["workflow_file"])
    os.makedirs(cfg["output_dir"], exist_ok=True)
    os.makedirs(os.path.dirname(cfg["database"]), exist_ok=True)
    return cfg


CFG = load_config()
ROOT = _ROOT