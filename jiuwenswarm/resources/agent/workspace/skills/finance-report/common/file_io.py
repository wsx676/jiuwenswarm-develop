# -*- coding: utf-8 -*-
"""原子文件写入（优化方案 11，来源：TradingAgents 工程细节）

问题：decision.json/scores_cache/run_stats/Portfolio.json 直接
open+json.dump，进程中断（Ctrl-C/掉电/超时被杀）会留下截断 JSON，
下次复现读到损坏文件报错或静默错口径。

设计：先写同目录 .tmp 再 os.replace() 原子替换——目标文件要么是
旧内容要么是新内容，永不出现半截状态（rag_retriever 索引落盘已
采用同款模式，此处统一收口）。
"""

import json
import os


def atomic_write_json(path: str, obj, indent=2) -> str:
    """JSON 原子写（tmp + os.replace），返回落盘路径

    与直接 json.dump 产物逐字节一致（同 encoding/ensure_ascii/indent），
    仅写入方式改为原子替换，不影响任何数据口径。
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # 失败清理临时文件，避免残留 .tmp 干扰目录巡检
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return path
