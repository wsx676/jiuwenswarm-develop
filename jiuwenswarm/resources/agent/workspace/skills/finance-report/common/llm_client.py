# -*- coding: utf-8 -*-
"""技能内公共能力：LLM 客户端（Anthropic 协议，MiniMax Token Plan 接入）

配置读取优先级：构造函数 config > 环境变量（与项目 .env 四件套一致）：
- API_BASE（默认 https://api.minimaxi.com/anthropic）
- API_KEY / ANTHROPIC_API_KEY
- MODEL_NAME（默认 MiniMax-M2）

网络注意：api.minimaxi.com 境内直连即可，须绕过系统代理（trust_env=False）。
"""

import json
import logging
import os
import re
from typing import List, Optional

logger = logging.getLogger(__name__)


def load_env_file() -> dict:
    """从项目根 .env 读取键值（不覆盖已存在的环境变量）"""
    env = {}
    path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), *[".."] * 7, ".env"))
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class LLMClient:
    """轻量 LLM 客户端（Anthropic messages 协议，requests 直连实现）"""

    def __init__(self, config: Optional[dict] = None):
        config = config or {}
        env = load_env_file()

        def pick(*keys, default=""):
            for k in keys:
                if config.get(k):
                    return config[k]
                if os.environ.get(k):
                    return os.environ[k]
                if env.get(k):
                    return env[k]
            return default

        self.api_base = pick("API_BASE", "LLM_API_BASE",
                             default="https://api.minimaxi.com/anthropic")
        self.api_key = pick("API_KEY", "ANTHROPIC_API_KEY",
                            "MINIMAX_API_KEY")
        self.model = pick("MODEL_NAME", "LLM_MODEL", default="MiniMax-M2")
        self.timeout = int(config.get("timeout", 120))

    def chat(self, prompt: str, system: str = "",
             max_tokens: int = 2048, temperature: float = 0.3) -> str:
        """单轮对话，返回模型正文（自动剥离 thinking 内容）"""
        import requests

        session = requests.Session()
        session.trust_env = False  # 境内站点直连，绕过系统代理
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        resp = session.post(
            f"{self.api_base.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload, timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        # Day 5 资源消耗记录：提取响应 usage 字段累计到遥测单例
        # （input_tokens/output_tokens，run_stats.json 落盘可追溯）
        try:
            from common.telemetry import RUN_STATS
            RUN_STATS.add_llm_usage(data.get("usage"))
        except Exception:  # noqa: BLE001 遥测失败不影响主流程
            pass
        texts = []
        for block in data.get("content", []):
            if block.get("type") == "text" and block.get("text"):
                texts.append(block["text"])
        return "".join(texts).strip()

    def chat_json(self, prompt: str, system: str = "",
                  max_tokens: int = 2048, temperature: float = 0.3):
        """对话并解析 JSON 输出；解析失败返回 None"""
        text = self.chat(prompt, system=system, max_tokens=max_tokens,
                         temperature=temperature)
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            # 兼容纯数组输出
            start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            logger.warning("LLM JSON 解析失败: %s", text[:200])
            return None

    @staticmethod
    def available(config: Optional[dict] = None) -> bool:
        """是否有可用配置（Key 缺失时调用方应走规则降级）"""
        try:
            return bool(LLMClient(config).api_key)
        except Exception:
            return False
