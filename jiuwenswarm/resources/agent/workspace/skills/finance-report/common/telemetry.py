# -*- coding: utf-8 -*-
"""运行遥测与可复现性保障（Day 5）

成果可复现性三件套：
1. 固定随机种子：全流程打分/仓位分配为确定性规则，无随机源；
   SEED 显式声明并经 fix_random_seed() 固定，写入 run_stats.json
2. 各阶段耗时：orchestrator 各阶段计时，随 run_stats.json 落盘
3. LLM 资源消耗：调用次数 / input_tokens / output_tokens 累计
   （LLMClient.chat 提取响应 usage 字段自动累计）

run_stats.json 落盘位置：{output_dir}/decision_log/run_stats.json，
保留最近 10 次运行记录（runs 数组），供第三方复放比对。
"""

import json
import os
import random
import time

# 全局可复现种子（固定值；技能内打分/配置均为确定性规则，
# 若未来引入随机源必须经 fix_random_seed 固定，保证结果可复现）
SEED = 20260819


def fix_random_seed(seed: int = SEED) -> int:
    """固定随机种子并返回实际使用的种子值"""
    random.seed(seed)
    return seed


class RunStats:
    """单次运行统计收集器（进程级累计，跨阶段共享）"""

    def __init__(self):
        self.seed = SEED
        self.phases = []      # [{"phase": 标题, "seconds": 耗时, "error": bool}]
        self.llm = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.failures = []    # [{"where": 阶段/标的, "detail": 摘要}]

    # ------------------------------------------------------------------
    def time_phase(self, title: str) -> "_PhaseTimer":
        """阶段计时上下文管理器：with RUN_STATS.time_phase("采集"): ..."""
        return _PhaseTimer(self, title)

    def add_llm_usage(self, usage) -> None:
        """累计一次 LLM 调用的 token 消耗（Anthropic usage 字段）"""
        if not isinstance(usage, dict):
            return
        self.llm["calls"] += 1
        self.llm["input_tokens"] += int(usage.get("input_tokens") or 0)
        self.llm["output_tokens"] += int(usage.get("output_tokens") or 0)

    def record_failure(self, where: str, detail) -> None:
        """失败留痕（批量运行单标的失败不阻断，但须可追溯）"""
        self.failures.append({"where": str(where), "detail": str(detail)[:500]})

    # ------------------------------------------------------------------
    def summary(self) -> dict:
        return {
            "seed": self.seed,
            "phases": list(self.phases),
            "total_seconds": round(
                sum(p["seconds"] for p in self.phases), 2),
            "llm": dict(self.llm),
            "failures": list(self.failures),
        }

    def save(self, output_dir: str) -> str:
        """落盘 run_stats.json（保留最近 10 次运行记录）"""
        log_dir = os.path.join(output_dir, "decision_log")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "run_stats.json")
        runs = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    prev = json.load(f)
                runs = prev.get("runs") or ([prev] if prev else [])
            except (OSError, json.JSONDecodeError):
                runs = []
        runs.append(self.summary())
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"runs": runs[-10:]}, f, ensure_ascii=False, indent=2)
        return path


class _PhaseTimer:
    """阶段计时器（不吞异常，失败同样留痕）"""

    def __init__(self, stats: RunStats, title: str):
        self.stats = stats
        self.title = title
        self.start = 0.0

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        seconds = round(time.perf_counter() - self.start, 2)
        self.stats.phases.append(
            {"phase": self.title, "seconds": seconds,
             "error": exc_type is not None})
        if exc_type is not None:
            self.stats.record_failure(self.title, exc)
        return False


# 进程级单例：orchestrator / LLMClient 共享同一份统计
RUN_STATS = RunStats()
