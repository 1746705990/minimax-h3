"""提示词内容审核：本地敏感词 + 可选远程审核 API 钩子。

公开服务必须做内容过滤。默认用项目根目录 banned_words.txt（一行一个词）。
如需接入第三方文本审核（如你在用的积算科技 MaaS、百度/阿里内容安全），
在 config.yaml 同级加 moderation_api 配置并实现 call_remote_audit()。
"""
import os

from .config import CFG, ROOT

_WORDS = None


def _load_words():
    global _WORDS
    if _WORDS is not None:
        return _WORDS
    _WORDS = []
    path = os.path.join(ROOT, "banned_words.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            _WORDS = [w.strip() for w in f if w.strip() and not w.startswith("#")]
    return _WORDS


def reload_words():
    global _WORDS
    _WORDS = None
    _load_words()


def check_prompt(prompt: str) -> tuple[bool, str]:
    """返回 (是否通过, 拒绝原因)"""
    p = prompt.strip()
    if not p:
        return False, "提示词不能为空"
    max_len = CFG.get("max_prompt_length", 4000)
    if len(p) > max_len:
        return False, f"提示词过长（最多 {max_len} 字）"
    for w in _load_words():
        if w in p:
            return False, "提示词包含不允许的内容，请修改后重试"
    return True, ""