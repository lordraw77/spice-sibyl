"""Tests for the Phase 28.d GraphRAG layer: community detection, LLM extraction,
community build and global search (map-reduce). LLM calls are stubbed so the
tests are deterministic and offline."""

import asyncio
import os
import tempfile

import aiosqlite
import pytest

from app.db import graph_repository as graph_repo
from app.db import kb_repository as repo
from app.db.database import _SCHEMA
from app.services import embedding_service, graph_service, graphrag_service, rag_service

_VOCAB = ["acme", "rocket", "sdk", "globex", "widget", "finance", "cat"]


def _vec(text: str) -> list[float]:
    t = text.lower()
    return [float(t.count(w) + 1) for w in _VOCAB]


async def _fake_embed(texts):
    return [_vec(t) for t in texts], "stub:test"


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    monkeypatch.setattr(embedding_service, "embed_texts", _fake_embed)
    monkeypatch.setattr(rag_service.embedding_service, "embed_texts", _fake_embed)


async def _make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await db.commit()
    await db.execute("PRAGMA foreign_keys=ON")
    return db, path


# ── community detection (pure function) ────────────────────────
def test_detect_communities_splits_two_clusters():
    # Two triangles with no cross edges → two communities.
    nodes = ["a", "b", "c", "x", "y", "z"]
    adj = [
        ("a", "b", 1.0), ("b", "c", 1.0), ("a", "c", 1.0),
        ("x", "y", 1.0), ("y", "z", 1.0), ("x", "z", 1.0),
    ]
    labels = graphrag_service.detect_communities(nodes, adj)
    assert labels["a"] == labels["b"] == labels["c"]
    assert labels["x"] == labels["y"] == labels["z"]
    assert labels["a"] != labels["x"]


def test_detect_communities_deterministic():
    nodes = ["n1", "n2", "n3", "n4"]
    adj = [("n1", "n2", 2.0), ("n2", "n3", 1.0), ("n3", "n4", 3.0)]
    first = graphrag_service.detect_communities(nodes, adj)
    second = graphrag_service.detect_communities(nodes, adj)
    assert first == second


def test_detect_communities_empty():
    assert graphrag_service.detect_communities([], []) == {}


# ── build_communities over an ingested graph ───────────────────
def test_build_communities_groups_comentioned_entities():
    async def run():
        db, path = await _make_db()
        try:
            # Two documents, each with its own repeated multi-word entities so the
            # regex extractor emits them and co-mention adjacency clusters them.
            md1 = "# Acme\n" + "Acme Rocket and Acme SDK ship together. " * 6
            md2 = "# Globex\n" + "Globex Widget and Globex Finance report. " * 6
            for name, md in (("acme.md", md1), ("globex.md", md2)):
                did = await repo.create_document(db, "default", name, "text/markdown", len(md))
                await rag_service.ingest(db, did, "default", name, md.encode())

            created, summarised, entities = await graphrag_service.build_communities(db, "default")
            assert entities > 0
            assert created >= 1
            # Extractive summaries by default (community summary flag off).
            assert summarised == 0

            comms = await graph_repo.list_communities(db, "default")
            assert comms, "expected at least one community node"
            # Community nodes must NOT leak into the normal entity graph view.
            nodes, _edges = await graph_repo.get_graph(db, "default")
            assert all(n.type != "community" for n in nodes)
        finally:
            await db.close()
            os.unlink(path)

    asyncio.run(run())


# ── LLM extraction path (stubbed) ──────────────────────────────
def test_llm_extraction_adds_related_edges(monkeypatch):
    async def run():
        db, path = await _make_db()

        async def fake_llm(prompt, model, max_tokens=700):
            return (
                '{"entities": [{"name": "Acme Corp", "type": "org"}, '
                '{"name": "Rocket Engine", "type": "product"}], '
                '"relationships": [{"source": "Acme Corp", "target": "Rocket Engine", '
                '"description": "builds"}]}'
            )

        monkeypatch.setattr(graphrag_service, "_llm", fake_llm)
        monkeypatch.setattr(graph_service, "extract_entities", lambda md: [])
        from app.core.config import settings
        monkeypatch.setattr(settings, "graph_llm_extract", True)

        try:
            md = "# Doc\nAcme Corp builds the Rocket Engine for launches."
            did = await repo.create_document(db, "default", "d.md", "text/markdown", len(md))
            await rag_service.ingest(db, did, "default", "d.md", md.encode())

            adj = await graph_repo.get_entity_adjacency(db, "default")
            # The 'related' edge from the LLM must appear in the adjacency.
            assert adj, "expected entity adjacency from related/co-mention edges"
            nodes, _e = await graph_repo.get_graph(db, "default")
            labels = {n.label for n in nodes}
            assert "Acme Corp" in labels and "Rocket Engine" in labels
        finally:
            await db.close()
            os.unlink(path)

    asyncio.run(run())


# ── global search (map-reduce, stubbed LLM) ────────────────────
def test_global_search_map_reduce(monkeypatch):
    async def run():
        db, path = await _make_db()

        async def fake_llm(prompt, model, max_tokens=700):
            if "score" in prompt and "point" in prompt:
                # map step
                if "Acme" in prompt:
                    return '{"score": 90, "point": "Acme builds rockets."}'
                return '{"score": 0, "point": ""}'
            # reduce step
            return "Acme builds rockets."

        monkeypatch.setattr(graphrag_service, "_llm", fake_llm)

        try:
            md1 = "# Acme\n" + "Acme Rocket and Acme SDK ship together. " * 6
            md2 = "# Globex\n" + "Globex Widget and Globex Finance report. " * 6
            for name, md in (("acme.md", md1), ("globex.md", md2)):
                did = await repo.create_document(db, "default", name, "text/markdown", len(md))
                await rag_service.ingest(db, did, "default", name, md.encode())
            await graphrag_service.build_communities(db, "default")

            result = await graphrag_service.global_search(db, "default", "What does Acme build?")
            assert "rocket" in result["answer"].lower()
            assert result["points"], "expected at least one contributing community"
            assert all(p["score"] > 0 for p in result["points"])
        finally:
            await db.close()
            os.unlink(path)

    asyncio.run(run())


def test_global_search_no_communities():
    async def run():
        db, path = await _make_db()
        try:
            result = await graphrag_service.global_search(db, "default", "anything")
            assert result["points"] == []
            assert "community" in result["answer"].lower()
        finally:
            await db.close()
            os.unlink(path)

    asyncio.run(run())
