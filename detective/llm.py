"""大模型客户端（OpenAI 兼容）。

分工：
  - 拆子主张（claims）
  - 常识短路的判定（claims）
  - 给单条证据打立场标签（investigator）
  - **读证据下判定**（judge.llm_assess）——但必须点名依据哪条证据，
    且该证据权威度要达标，算法会校验；不达标就不采纳。
结论不接受模型自由发挥：模型给的方向必须能被某条真实证据支撑，否则退回规则的拒答。
没有密钥时全部走规则分支，流程照常跑完。
"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

from . import config

_SYSTEM = (
    "你是一个严谨的事实核查助理。只输出 JSON，不要输出任何解释文字、不要用 markdown 代码块。"
)


def available() -> bool:
    return config.has_llm_key()


def chat_json(prompt: str, *, max_tokens: int = 700) -> dict[str, Any] | None:
    """调用模型并要求返回 JSON。任何异常都返回 None，调用方须有规则兜底。"""
    if not available():
        return None
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {config.LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=config.LLM_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception:
        return None
    return _loose_json(content)


def _loose_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None
