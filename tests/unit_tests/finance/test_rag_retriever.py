# -*- coding: utf-8 -*-
"""RAGRetriever 单元测试：混合分块、本地向量化、双路召回检索、
冷启动播种（全部本地计算，mock 掉 MiniMax 探测，无网络请求）"""

import os

import pytest

from collectors.rag_retriever import (RAGRetriever, KnowledgeChunk,
                                      split_markdown, tokenize)


@pytest.fixture
def local_rag(tmp_path, monkeypatch):
    """禁用远端 embedding 探测，强制本地向量化路径的临时知识库"""
    monkeypatch.setattr(RAGRetriever, "_try_remote",
                        lambda self, chunks, batch=16: False)
    return RAGRetriever({"kb_dir": str(tmp_path / "kb")})


DOCS = [
    {"title": "白酒估值方法", "source": "白酒估值方法.md",
     "content": ("# 白酒估值方法\n\n## 相对估值\n"
                 "高端白酒盈利稳定，常用 PE 估值并参考历史分位。\n\n"
                 "## 绝对估值\nDCF 自由现金流折现适用于现金流优异的公司。")},
    {"title": "半导体周期", "source": "半导体周期.md",
     "content": ("# 半导体周期\n\n半导体是强周期行业，跟踪全球销售额、"
                 "库存水位与资本开支。")},
]


class TestSplitMarkdown:
    def test_heading_path(self):
        chunks = split_markdown("估值方法", "# 估值方法\n\n## 相对估值\nPE 对比\n\n## 绝对估值\nDCF 折现")
        headings = [c["heading"] for c in chunks]
        assert "估值方法 > 相对估值" in headings
        assert "估值方法 > 绝对估值" in headings

    def test_long_chunk_split(self):
        long = "\n".join(f"段落{i}内容测试文本" * 10 for i in range(20))
        chunks = split_markdown("t", f"# t\n\n{long}", max_chars=400)
        assert len(chunks) > 1
        assert all(len(c["content"]) <= 500 for c in chunks)

    def test_empty_content_skipped(self):
        assert split_markdown("t", "# t\n\n## 空节\n") == []


class TestTokenize:
    def test_chinese_bigram_and_ascii(self):
        toks = tokenize("ROE 杜邦分析")
        assert "roe" in toks
        assert "杜邦" in toks and "邦分" in toks

    def test_number(self):
        assert "600519" in tokenize("代码 600519")


class TestRetrieve:
    def test_relevant_doc_ranked_first(self, local_rag):
        local_rag.add_documents(DOCS)
        hits = local_rag.retrieve("白酒行业估值方法", top_k=2)
        assert len(hits) == 2
        assert hits[0].source == "白酒估值方法.md"
        assert hits[0].score > 0
        assert hits[0].heading  # 带标题路径上下文

    def test_retrieve_returns_knowledge_chunk(self, local_rag):
        local_rag.add_documents(DOCS)
        hits = local_rag.retrieve("半导体景气度", top_k=1)
        assert isinstance(hits[0], KnowledgeChunk)
        assert hits[0].source == "半导体周期.md"

    def test_empty_kb_returns_empty(self, local_rag):
        assert local_rag.retrieve("任意查询") == []

    def test_add_documents_returns_chunk_count(self, local_rag):
        n = local_rag.add_documents(DOCS)
        assert n >= 3  # 白酒 2 节 + 半导体 1 节
        # 重复添加会继续累加
        assert local_rag.add_documents(DOCS) == n


class TestPersistence:
    def test_new_instance_loads_saved_index(self, tmp_path, monkeypatch):
        """H3 回归：索引落盘后新实例可直接加载检索"""
        monkeypatch.setattr(RAGRetriever, "_try_remote",
                            lambda self, chunks, batch=16: False)
        kb = str(tmp_path / "kb")
        RAGRetriever({"kb_dir": kb}).add_documents(DOCS)
        assert os.path.exists(os.path.join(kb, "index", "index.json"))
        hits = RAGRetriever({"kb_dir": kb}).retrieve("白酒行业估值方法")
        assert hits and hits[0].source == "白酒估值方法.md"

    def test_corrupted_index_rebuilt_from_docs(self, tmp_path, monkeypatch):
        """H3 回归：索引损坏（截断 JSON）时自动从 docs 重建，不抛异常"""
        monkeypatch.setattr(RAGRetriever, "_try_remote",
                            lambda self, chunks, batch=16: False)
        kb = tmp_path / "kb"
        rag = RAGRetriever({"kb_dir": str(kb)})
        rag.ensure_kb()
        with open(rag.index_file, "w", encoding="utf-8") as f:
            f.write("{broken")
        rag2 = RAGRetriever({"kb_dir": str(kb)})
        hits = rag2.retrieve("白酒行业估值方法", top_k=2)
        assert hits and all(h.source for h in hits)


class TestColdStart:
    def test_ensure_kb_seeds_docs(self, local_rag):
        n = local_rag.ensure_kb()
        assert n > 0
        md_files = [f for f in os.listdir(local_rag.docs_dir)
                    if f.endswith(".md")]
        assert len(md_files) >= 10
        assert os.path.exists(local_rag.index_file)
        # 播种后可直接检索，且命中带来源
        hits = local_rag.retrieve("白酒行业估值方法", top_k=3)
        assert hits and all(h.source for h in hits)

    def test_ensure_kb_idempotent(self, local_rag):
        n1 = local_rag.ensure_kb()
        local_rag._index = None
        n2 = local_rag.ensure_kb()
        assert n1 == n2  # 已有 docs 时不重复播种
