# -*- coding: utf-8 -*-
"""技能内公共能力：LLM 客户端（Anthropic 协议，MiniMax Token Plan 接入）

配置读取优先级：构造函数 config > 环境变量（与项目 .env 四件套一致）：
- API_BASE（默认 https://api.minimaxi.com/anthropic）
- API_KEY / ANTHROPIC_API_KEY
- MODEL_NAME（默认 MiniMax-M2）

主备容灾（优化方案 8，来源：得物）：
- 可重试错误（5xx/限流 429/超时/网络异常）→ 自动切备用模型重试一次
  （默认智谱 GLM：ZHIPU_API_KEY，embedding 已在用同一 Key）
- 4xx（参数/内容错误）→ 不切换直接抛出，由调用方降级并留痕
  （避免掩盖真实错误）
- 切换事件写入 run_stats.json（llm_failover 计数）
- config llm.failover_enabled=False 可关闭（默认开：仅失败路径生效，
  成功调用口径不变，不影响确定性复现）

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

        # 方案 8 主备容灾：备用模型默认智谱 GLM（OpenAI 兼容协议），
        # Key 复用 embedding 已接入的 ZHIPU_API_KEY；无 Key 则不启用
        self.failover_enabled = bool(config.get("failover_enabled", True))
        self.backup_api_base = pick(
            "BACKUP_API_BASE", "ZHIPU_CHAT_API_BASE",
            default="https://open.bigmodel.cn/api/paas/v4")
        self.backup_api_key = pick("BACKUP_API_KEY", "ZHIPU_API_KEY")
        self.backup_model = pick(
            "BACKUP_MODEL_NAME", "ZHIPU_CHAT_MODEL", default="glm-4-flash")
        self.backup_is_openai = bool(
            config.get("backup_openai_protocol", True))

    # ------------------------------------------------------------------
    def chat(self, prompt: str, system: str = "",
             max_tokens: int = 2048, temperature: float = 0.3) -> str:
        """单轮对话，返回模型正文（自动剥离 thinking 内容）

        主模型可重试错误（5xx/限流/超时/网络异常）自动切备用模型重试
        一次；4xx 不切换直接抛出（调用方降级留痕）。
        """
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        try:
            data = self._post(self.api_base, self.api_key, self.model,
                              payload, openai=False)
        except Exception as e:  # noqa: BLE001 容灾判定需看异常全类型
            if not self._should_failover(e):
                raise
            logger.warning(
                "主模型 %s 调用失败（%s），切换备用模型 %s 重试",
                self.model, e, self.backup_model)
            self._record_failover()
            data = self._post(self.backup_api_base, self.backup_api_key,
                              self.backup_model, payload,
                              openai=self.backup_is_openai)
        # Day 5 资源消耗记录：提取响应 usage 字段累计到遥测单例
        # （input_tokens/output_tokens，run_stats.json 落盘可追溯；
        # 主备两路响应均经此累计，资源消耗数据保持完整）
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

    # ------------------------------------------------------------------
    def _post(self, api_base: str, api_key: str, model: str,
              payload: dict, openai: bool) -> dict:
        """单路 HTTP 调用（Anthropic / OpenAI 兼容协议），返回响应 JSON

        OpenAI 兼容协议（智谱 GLM）：payload 转换 + 响应归一为
        Anthropic 风格（content/usage），上层解析逻辑单一。
        """
        import requests

        session = requests.Session()
        session.trust_env = False  # 境内站点直连，绕过系统代理
        if openai:
            body = {
                "model": model,
                "max_tokens": payload["max_tokens"],
                "temperature": payload["temperature"],
                "messages": list(payload["messages"]),
            }
            if payload.get("system"):
                body["messages"].insert(
                    0, {"role": "system", "content": payload["system"]})
            resp = session.post(
                f"{api_base.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "content-type": "application/json"},
                json=body, timeout=self.timeout,
            )
            resp.raise_for_status()
            raw = resp.json()
            # 归一为 Anthropic 风格，上层单一解析路径
            choice = (raw.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content", "") or ""
            usage = raw.get("usage") or {}
            return {"content": [{"type": "text", "text": text}],
                    "usage": {
                        "input_tokens": usage.get("prompt_tokens", 0),
                        "output_tokens": usage.get("completion_tokens", 0),
                    }}
        resp = session.post(
            f"{api_base.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={**payload, "model": model}, timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _should_failover(self, exc: Exception) -> bool:
        """是否切换备用模型：开关开 + 备用 Key 就绪 + 可重试错误"""
        if not (self.failover_enabled and self.backup_api_key):
            return False
        return self._is_retryable(exc)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """可重试错误判定：5xx/限流 429/超时/网络异常；其余 4xx 不切换"""
        import requests
        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        resp = getattr(exc, "response", None)
        if resp is not None:
            return resp.status_code == 429 or resp.status_code >= 500
        return False

    @staticmethod
    def _record_failover() -> None:
        """切换事件留痕 run_stats.json（遥测失败不影响主流程）"""
        try:
            from common.telemetry import RUN_STATS
            RUN_STATS.add_llm_failover()
        except Exception:  # noqa: BLE001
            pass

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
