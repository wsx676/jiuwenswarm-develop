# -*- coding: utf-8 -*-
"""财务知识 RAG 检索器

将财务分析方法论、行业知识、估值模型说明等沉淀为向量知识库，
生成研报时检索相关知识片段，增强专业性与准确性，降低幻觉。

检索三件套设计：
- 混合分块：按标题/段落语义切块，保留标题路径上下文（如"估值方法 > 相对估值"）
- 混合检索：向量相似度 + 关键词（BM25）双路召回
- 重排：RRF 融合 + query 词命中加权，取 top_k 注入上下文

Embedding 双层策略：
1. 主路径：智谱 embedding-3（ZHIPU_API_KEY，2048 维；MiniMax Token Plan
   订阅 Key 实测不支持 embedding 接口，故不用于向量化）
2. 兜底：本地零依赖向量化（字符 bigram TF-IDF），离线可用、确定性可复现

存储：kb_dir/docs/ 存知识文档，kb_dir/index/index.json 存索引
（含分词与向量，支持离线重建：build(kb_dir/docs)）。
"""

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-zA-Z]+|\d+(?:\.\d+)?")


def tokenize(text: str) -> List[str]:
    """分词：ASCII 词/数字小写化 + 中文连续串切字符与 bigram（零依赖）"""
    tokens = []
    for m in _WORD_RE.finditer(text):
        tokens.append(m.group(0).lower())
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(seg)
        tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return tokens


@dataclass
class KnowledgeChunk:
    """知识片段"""
    content: str
    source: str        # 来源文档
    score: float = 0.0  # 融合相关性得分
    heading: str = ""   # 标题路径上下文
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "content": self.content, "source": self.source,
            "score": round(self.score, 4), "heading": self.heading,
        }


def split_markdown(title: str, text: str,
                   max_chars: int = 400) -> List[dict]:
    """混合分块：按标题层级切块并保留标题路径；超长块按段落再切（带重叠）"""
    chunks: List[dict] = []
    path = [title] if title else []
    buf: List[str] = []

    def flush():
        content = "\n".join(buf).strip()
        buf.clear()
        if not content:
            return
        heading = " > ".join(path)
        if len(content) <= max_chars:
            chunks.append({"heading": heading, "content": content})
            return
        # 超长块按段落切分，保留尾部上下文作为下一块前缀（重叠）
        cur = ""
        for p in content.split("\n"):
            if cur and len(cur) + len(p) + 1 > max_chars:
                chunks.append({"heading": heading, "content": cur})
                cur = cur[-80:] + ("\n" + p if p else "")
            else:
                cur = f"{cur}\n{p}" if cur else p
        if cur.strip():
            chunks.append({"heading": heading, "content": cur})

    for line in text.split("\n"):
        h = re.match(r"^(#{1,3})\s+(.+)$", line)
        if h:
            flush()
            path = path[:len(h.group(1)) - 1] + [h.group(2).strip()]
        else:
            buf.append(line)
    flush()
    return chunks


class RAGRetriever:
    """财务知识 RAG 检索器"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        # 知识库目录：财务方法论、行业研究框架、估值模型等
        default_kb = os.path.abspath(os.path.join(
            os.path.dirname(__file__), *[".."] * 7,
            "reports", "finance-report", "finance_kb"))
        self.kb_dir = self.config.get("kb_dir", default_kb)
        self.docs_dir = os.path.join(self.kb_dir, "docs")
        self.index_file = os.path.join(self.kb_dir, "index", "index.json")
        self.top_recall = int(self.config.get("top_recall", 20))
        self._index: Optional[dict] = None
        self._remote_failed = False  # 远端 embedding 探测失败缓存，避免重复网络尝试

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def retrieve(self, query: str, top_k: int = 5) -> List[KnowledgeChunk]:
        """检索相关知识片段：向量 + BM25 双路召回 → RRF 融合重排"""
        index = self._load_index()
        chunks = index.get("chunks", [])
        if not chunks:
            logger.warning("知识库为空，先调用 ensure_kb()/build() 构建")
            return []

        q_tokens = tokenize(query)
        vec_scores = self._vector_scores(index, query, q_tokens)
        bm25_scores = self._bm25_scores(index, q_tokens)

        # RRF 融合双路召回
        rrf: Dict[int, float] = {}
        for scores in (vec_scores, bm25_scores):
            ranked = sorted(scores.items(), key=lambda x: -x[1])
            for rank, (cid, _s) in enumerate(ranked[:self.top_recall]):
                rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (60 + rank + 1)

        # 重排加权：query bigram 在块内的命中率
        q_bigrams = set(t for t in q_tokens if len(t) >= 2)
        for cid in list(rrf):
            toks = set(chunks[cid].get("tokens", {}))
            hit = len(q_bigrams & toks) / max(len(q_bigrams), 1)
            rrf[cid] *= (1.0 + 0.5 * hit)

        total = sum(rrf.values()) or 1.0
        ranked_ids = sorted(rrf.items(), key=lambda x: -x[1])[:top_k]
        return [
            KnowledgeChunk(
                content=chunks[cid]["content"],
                source=chunks[cid]["source"],
                score=s / total,
                heading=chunks[cid].get("heading", ""))
            for cid, s in ranked_ids
        ]

    # ------------------------------------------------------------------
    # 知识库构建
    # ------------------------------------------------------------------
    def add_documents(self, docs: List[dict]) -> int:
        """向知识库添加文档（{"title","content","source"?}），返回新增块数"""
        index = self._load_index()
        new_chunks = []
        for doc in docs:
            title = doc.get("title", "")
            source = doc.get("source", title)
            for part in split_markdown(title, doc.get("content", "")):
                tokens = tokenize(part["content"])
                if not tokens:
                    continue
                tf: Dict[str, int] = {}
                for t in tokens:
                    tf[t] = tf.get(t, 0) + 1
                new_chunks.append({
                    "content": part["content"], "source": source,
                    "heading": part["heading"], "tokens": tf,
                })
        if new_chunks:
            self._embed_chunks(index, new_chunks)
            self._rebuild_stats(index)
            self._save_index(index)
        return len(new_chunks)

    def build(self, docs_dir: Optional[str] = None) -> int:
        """从 docs 目录重建知识库（*.md，首个 # 为标题）"""
        docs_dir = docs_dir or self.docs_dir
        if not os.path.isdir(docs_dir):
            logger.warning("知识库文档目录不存在: %s", docs_dir)
            return 0
        self._index = {"chunks": []}
        docs = []
        for name in sorted(os.listdir(docs_dir)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(docs_dir, name), encoding="utf-8") as f:
                text = f.read()
            m = re.match(r"^#\s+(.+)$", text, re.M)
            title = m.group(1).strip() if m else name[:-3]
            docs.append({"title": title, "content": text, "source": name})
        return self.add_documents(docs)

    def ensure_kb(self) -> int:
        """冷启动：docs 为空时自动播种内置财务方法论文档并建索引"""
        has_docs = os.path.isdir(self.docs_dir) and any(
            f.endswith(".md") for f in os.listdir(self.docs_dir))
        if not has_docs:
            from collectors.kb_seed import SEED_DOCS
            os.makedirs(self.docs_dir, exist_ok=True)
            for doc in SEED_DOCS:
                fname = re.sub(r"[^\w\u4e00-\u9fff-]+", "_",
                               doc["title"]) + ".md"
                with open(os.path.join(self.docs_dir, fname),
                          "w", encoding="utf-8") as f:
                    f.write(f"# {doc['title']}\n\n{doc['content']}")
            logger.info("知识库冷启动：播种 %d 篇方法论文档", len(SEED_DOCS))
        if os.path.exists(self.index_file) and has_docs:
            self._index = None
            return len(self._load_index().get("chunks", []))
        return self.build()

    # ------------------------------------------------------------------
    # 向量化（智谱 embedding-3 优先，本地 TF-IDF 兜底）
    # ------------------------------------------------------------------
    def _embed_chunks(self, index: dict, new_chunks: List[dict]):
        """向量化新块并并入 index（embedder 升级/降级时保持向量空间一致）"""
        for c in new_chunks:
            c.pop("vec", None)
        index["chunks"].extend(new_chunks)
        if index.get("embedder") == "local":
            # 本地向量的 idf 依赖全库语料，需整体重算
            for c in index["chunks"]:
                c.pop("vec", None)
            self._embed_local(index)
            return
        # 远端路径（None→zhipu 或 zhipu 增量）：只向量化新块，
        # 避免增量添加时全库重算的 O(N²) API 成本（同一模型向量空间一致）
        if self._try_remote(new_chunks):
            index["embedder"] = "zhipu"
            return
        # 远端不可用：降级本地，全库重算（清理旧向量保证空间一致）
        for c in index["chunks"]:
            c.pop("vec", None)
        self._embed_local(index)

    def _try_remote(self, chunks: List[dict], batch: int = 16) -> bool:
        """尝试智谱 embedding-3；任何失败返回 False 走本地兜底"""
        if self._remote_failed:
            return False
        try:
            import requests
        except ImportError:
            return False
        api_key = self._env("ZHIPU_API_KEY")
        base = self._env("ZHIPU_EMBED_API_BASE") \
            or "https://open.bigmodel.cn/api/paas/v4"
        model = self._env("ZHIPU_EMBED_MODEL") or "embedding-3"
        if not api_key:
            return False
        session = requests.Session()
        session.trust_env = False
        try:
            for i in range(0, len(chunks), batch):
                part = chunks[i:i + batch]
                texts = [(c["heading"] + "\n" if c.get("heading") else "")
                         + c["content"] for c in part]
                resp = session.post(
                    f"{base.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": model, "input": texts},
                    timeout=60)
                resp.raise_for_status()
                body = resp.json()
                if body.get("error"):
                    raise RuntimeError(body["error"])
                # 智谱格式 data[].embedding（按 index 排序）；
                # 兼容 MiniMax 格式 vectors（按量 Key 场景）
                if body.get("data"):
                    data = sorted(body["data"],
                                  key=lambda d: d.get("index", 0))
                    vectors = [d["embedding"] for d in data]
                else:
                    br = body.get("base_resp") or {}
                    if br.get("status_code", 0) != 0:
                        raise RuntimeError(
                            f"status {br.get('status_code')}: "
                            f"{br.get('status_msg')}")
                    vectors = body.get("vectors") or []
                if len(vectors) != len(part):
                    self._remote_failed = True
                    return False
                for c, v in zip(part, vectors):
                    c["vec"] = [round(float(x), 5) for x in v]
                    c["norm"] = round(
                        math.sqrt(sum(x * x for x in c["vec"])), 6)
            return True
        except Exception as e:
            logger.warning("智谱 embedding 不可用（%s），降级本地向量化", e)
            self._remote_failed = True
            for c in chunks:
                c.pop("vec", None)
            return False

    def _embed_local(self, index: dict):
        """本地零依赖向量化：字符 bigram TF-IDF（稀疏向量）"""
        chunks = index["chunks"]
        n = len(chunks)
        df: Dict[str, int] = {}
        for c in chunks:
            for t in set(c["tokens"]):
                df[t] = df.get(t, 0) + 1
        for c in chunks:
            vec, norm = {}, 0.0
            for t, tf in c["tokens"].items():
                w = ((1 + math.log(tf)) *
                     (math.log((n + 1) / (df[t] + 1)) + 1))
                vec[t] = round(w, 5)
                norm += w * w
            c["vec"] = vec
            c["norm"] = round(math.sqrt(norm), 6) if norm else 0.0
        index["embedder"] = "local"

    def _env(self, key: str) -> str:
        if self.config.get(key):
            return self.config[key]
        if os.environ.get(key):
            return os.environ[key]
        try:
            from common.llm_client import load_env_file
        except ImportError:
            import sys
            _p = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _p not in sys.path:
                sys.path.insert(0, _p)
            from common.llm_client import load_env_file
        return load_env_file().get(key, "")

    # ------------------------------------------------------------------
    # 打分
    # ------------------------------------------------------------------
    def _vector_scores(self, index: dict, query: str,
                       q_tokens: List[str]) -> Dict[int, float]:
        chunks = index.get("chunks", [])
        scores: Dict[int, float] = {}
        if index.get("embedder") == "zhipu":
            probe = [{"content": query, "heading": "", "tokens": {}}]
            if self._try_remote(probe):
                qv, qn = probe[0]["vec"], probe[0]["norm"] or 1.0
                for i, c in enumerate(chunks):
                    if isinstance(c.get("vec"), list):
                        dot = sum(a * b for a, b in zip(qv, c["vec"]))
                        scores[i] = dot / (qn * (c.get("norm") or 1.0))
                return scores
            logger.warning("query 向量化失败，向量路召回降级本地")
        # 本地 TF-IDF 余弦
        tf: Dict[str, int] = {}
        for t in q_tokens:
            tf[t] = tf.get(t, 0) + 1
        n = len(chunks)
        df = index.get("df", {})
        qvec, qnorm = {}, 0.0
        for t, c in tf.items():
            w = ((1 + math.log(c)) *
                 (math.log((n + 1) / (df.get(t, 0) + 1)) + 1))
            qvec[t] = w
            qnorm += w * w
        qnorm = math.sqrt(qnorm) or 1.0
        for i, c in enumerate(chunks):
            vec, norm = c.get("vec", {}), c.get("norm") or 1.0
            if isinstance(vec, dict):
                dot = sum(qvec.get(t, 0) * w for t, w in vec.items())
                if dot:
                    scores[i] = dot / (qnorm * norm)
        return scores

    def _bm25_scores(self, index: dict,
                     q_tokens: List[str]) -> Dict[int, float]:
        chunks = index.get("chunks", [])
        stats = index.get("stats", {})
        avg_len = stats.get("avg_len") or 1.0
        df, n = index.get("df", {}), max(len(chunks), 1)
        k1, b = 1.5, 0.75
        scores: Dict[int, float] = {}
        for i, c in enumerate(chunks):
            toks = c.get("tokens", {})
            dlen = sum(toks.values()) or 1
            s = 0.0
            for t in set(q_tokens):
                f = toks.get(t, 0)
                if not f:
                    continue
                idf = math.log((n - df.get(t, 0) + 0.5) /
                               (df.get(t, 0) + 0.5) + 1)
                s += idf * f * (k1 + 1) / (
                    f + k1 * (1 - b + b * dlen / avg_len))
            if s > 0:
                scores[i] = s
        return scores

    def _rebuild_stats(self, index: dict):
        chunks = index["chunks"]
        df: Dict[str, int] = {}
        total_len = 0
        for c in chunks:
            total_len += sum(c["tokens"].values())
            for t in set(c["tokens"]):
                df[t] = df.get(t, 0) + 1
        index["df"] = df
        index["stats"] = {
            "n": len(chunks),
            "avg_len": total_len / max(len(chunks), 1),
        }

    # ------------------------------------------------------------------
    # 索引存取
    # ------------------------------------------------------------------
    def _load_index(self) -> dict:
        if self._index is not None:
            return self._index
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, encoding="utf-8") as f:
                    self._index = json.load(f)
            except (json.JSONDecodeError, OSError):
                # 索引损坏（如写入中断留下的截断 JSON）：从 docs 自动重建
                logger.warning("索引文件损坏，从 docs 重建：%s",
                               self.index_file)
                self._index = {"chunks": []}
                if os.path.isdir(self.docs_dir):
                    self.build()
        else:
            self._index = {"chunks": []}
        return self._index

    def _save_index(self, index: dict):
        # 先写临时文件再原子替换，避免进程中断留下截断 JSON
        os.makedirs(os.path.dirname(self.index_file), exist_ok=True)
        tmp = self.index_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
        os.replace(tmp, self.index_file)
        logger.info("知识库索引已保存：%d 块（embedder=%s）",
                    len(index.get("chunks", [])), index.get("embedder"))
