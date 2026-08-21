# -*- coding: utf-8 -*-
"""LLM 主备容灾单元测试（优化方案 8）

覆盖：可重试错误判定（5xx/429/超时/网络 vs 4xx 不切换）、
故障注入切换备用模型、遥测留痕、OpenAI 兼容协议归一、开关关闭。
全部 mock，无真实网络调用。
"""

import requests

from common.llm_client import LLMClient
from common.telemetry import RUN_STATS


def _client():
    client = LLMClient({"API_KEY": "test-key"})
    client.backup_api_key = "backup-key"   # 测试内固定，不依赖环境
    return client


def _http_error(status_code: int):
    resp = requests.models.Response()
    resp.status_code = status_code
    return requests.exceptions.HTTPError(response=resp)


class TestRetryable:
    def test_timeout_retryable(self):
        assert LLMClient._is_retryable(requests.Timeout())

    def test_connection_error_retryable(self):
        assert LLMClient._is_retryable(requests.ConnectionError())

    def test_5xx_and_429_retryable(self):
        assert LLMClient._is_retryable(_http_error(500))
        assert LLMClient._is_retryable(_http_error(503))
        assert LLMClient._is_retryable(_http_error(429))

    def test_4xx_not_retryable(self):
        # 参数/内容错误不切换，避免掩盖真实错误
        assert not LLMClient._is_retryable(_http_error(400))
        assert not LLMClient._is_retryable(_http_error(401))

    def test_unknown_exception_not_retryable(self):
        assert not LLMClient._is_retryable(ValueError("bad json"))


class TestShouldFailover:
    def test_disabled_by_config(self):
        client = LLMClient({"API_KEY": "k", "failover_enabled": False})
        client.backup_api_key = "backup-key"
        assert not client._should_failover(requests.Timeout())

    def test_no_backup_key(self):
        client = LLMClient({"API_KEY": "k"})
        client.backup_api_key = ""
        assert not client._should_failover(requests.Timeout())

    def test_enabled_with_key(self):
        assert _client()._should_failover(requests.Timeout())


class TestFailoverFlow:
    def test_retryable_switches_to_backup(self, monkeypatch):
        """故障注入：主模型超时 → 切备用模型成功返回"""
        client = _client()
        calls = []

        def fake_post(api_base, api_key, model, payload, openai):
            calls.append((api_base, model, openai))
            if not openai:
                raise requests.Timeout("primary timeout")
            return {"content": [{"type": "text", "text": "备用回答"}],
                    "usage": {"input_tokens": 5, "output_tokens": 7}}

        monkeypatch.setattr(LLMClient, "_post",
                            staticmethod(fake_post))
        before = RUN_STATS.llm_failover
        assert client.chat("你好") == "备用回答"
        assert len(calls) == 2 and calls[1][2] is True
        assert RUN_STATS.llm_failover == before + 1

    def test_4xx_no_switch_raises(self, monkeypatch):
        """4xx 不切换：直接抛出由调用方降级留痕"""
        client = _client()

        def fake_post(api_base, api_key, model, payload, openai):
            raise _http_error(400)

        monkeypatch.setattr(LLMClient, "_post",
                            staticmethod(fake_post))
        before = RUN_STATS.llm_failover
        try:
            client.chat("你好")
            assert False, "应抛出 HTTPError"
        except requests.exceptions.HTTPError:
            pass
        assert RUN_STATS.llm_failover == before  # 未切换不留痕

    def test_backup_also_fails_raises(self, monkeypatch):
        """备用模型同样失败 → 抛出（调用方走规则降级）"""
        client = _client()

        def fake_post(api_base, api_key, model, payload, openai):
            raise requests.Timeout("down")

        monkeypatch.setattr(LLMClient, "_post",
                            staticmethod(fake_post))
        try:
            client.chat("你好")
            assert False, "应抛出 Timeout"
        except requests.Timeout:
            pass


class TestOpenaiProtocol:
    def test_zhipu_response_normalized(self, monkeypatch):
        """智谱 OpenAI 兼容响应归一为 Anthropic 风格（content/usage）"""
        client = _client()

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "GLM 回答"}}],
                        "usage": {"prompt_tokens": 3,
                                  "completion_tokens": 4}}

        posts = []
        monkeypatch.setattr(
            requests.Session, "post",
            lambda self, url, **kw: (posts.append((url, kw)), _Resp())[1])
        out = client._post(
            "https://open.bigmodel.cn/api/paas/v4", "bk", "glm-4-flash",
            {"max_tokens": 100, "temperature": 0.3,
             "messages": [{"role": "user", "content": "x"}],
             "system": "sys"},
            openai=True)
        assert out["content"][0]["text"] == "GLM 回答"
        assert out["usage"] == {"input_tokens": 3, "output_tokens": 4}
        url, kw = posts[0]
        assert url.endswith("/chat/completions")
        assert kw["json"]["messages"][0]["role"] == "system"
        assert "glm-4-flash" == kw["json"]["model"]

    def test_usage_recorded_on_backup_path(self, monkeypatch):
        """备用路径 usage 同样累计（资源消耗数据完整）"""
        client = _client()

        def fake_post(api_base, api_key, model, payload, openai):
            if not openai:
                raise requests.ConnectionError("reset")
            return {"content": [{"type": "text", "text": "ok"}],
                    "usage": {"input_tokens": 11, "output_tokens": 13}}

        monkeypatch.setattr(LLMClient, "_post", staticmethod(fake_post))
        before = dict(RUN_STATS.llm)
        client.chat("你好")
        assert RUN_STATS.llm["input_tokens"] >= before["input_tokens"] + 11
        assert RUN_STATS.llm["output_tokens"] >= before["output_tokens"] + 13


class TestTelemetrySummary:
    def test_failover_conditional_output(self):
        from common.telemetry import RunStats
        stats = RunStats()
        assert "llm_failover" not in stats.summary()
        stats.add_llm_failover()
        stats.add_llm_failover()
        assert stats.summary()["llm_failover"] == 2
