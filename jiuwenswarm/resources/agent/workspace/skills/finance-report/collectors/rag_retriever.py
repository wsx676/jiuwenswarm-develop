# -*- coding: utf-8 -*-
"""财务知识 RAG 检索器

将财务分析方法论、行业知识、估值模型说明等沉淀为向量知识库，
生成研报时检索相关知识片段，增强专业性与准确性，降低幻觉。

检索三件套设计：
- 混合分块：按标题/段落语义切块，保留表头上下文
- 混合检索：向量相似度 + 关键词（BM25）双路召回
- 重排：对召回结果按相关性重排，取 top_k 注入上下文
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class KnowledgeChunk:
    """知识片段"""
    content: str
    source: str        # 来源文档
    score: float       # 相似度得分
    metadata: dict = field(default_factory=dict)


class RAGRetriever:
    """财务知识 RAG 检索器"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        # 知识库目录：财务方法论、行业研究框架、估值模型等
        self.kb_dir = self.config.get(
            "kb_dir", "workspace/agent/memory/finance_kb"
        )

    def retrieve(
        self, query: str, top_k: int = 5
    ) -> List[KnowledgeChunk]:
        """检索相关知识片段

        Args:
            query: 检索 query（如"半导体行业景气度分析框架"）
            top_k: 返回数量
        """
        # TODO(Day 2): 实现三件套检索流程：
        # 1. 将 query 向量化（开源 Embedding 模型）
        # 2. 向量库 + 关键词双路召回
        # 3. 重排后返回带来源标注的 top_k 知识片段
        return []

    def add_documents(self, docs: List[dict]) -> int:
        """向知识库添加文档（构建/更新阶段使用）"""
        count = 0
        for doc in docs:
            chunks = self._split_and_embed(doc)
            self._store_vectors(chunks)
            count += len(chunks)
        return count

    def _split_and_embed(self, doc: dict) -> List[KnowledgeChunk]:
        """混合分块并向量化（按标题/段落语义切块，保留表头上下文）"""
        # TODO(Day 2): 实现混合分块与 Embedding
        return []

    def _store_vectors(self, chunks: List[KnowledgeChunk]):
        """存入向量库"""
        # TODO(Day 2): 写入本地向量库（如 FAISS / chroma）
        pass
