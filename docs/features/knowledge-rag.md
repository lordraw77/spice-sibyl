# Knowledge base and RAG

## Document ingestion

**What it does.** Uploads documents (PDF, TXT, DOCX, Markdown) into a per-profile knowledge base. Text is extracted, split into chunks (800 characters, 120 overlap), embedded through the `EMBEDDING_CHAIN` fallback chain (default: Ollama `nomic-embed-text` → Gemini → Mistral) and stored as float32 BLOB vectors in SQLite (`kb_documents` / `kb_chunks`).

**How to use it.** Dedicated **Knowledge** page (`/knowledge`, **Risorse → Knowledge** in the navbar, or the *Gestisci →* link next to the RAG switch in the sidebar): upload one or more files, browse the document list, re-index or delete what you no longer need. API: `GET/POST/DELETE /v1/knowledge/documents`, `POST /v1/knowledge/search`.

## URL ingestion

**What it does.** `POST /v1/knowledge/urls` fetches a web page (full-text HTML extraction, same approach as the `read_url` tool) and indexes it like an upload. Web-sourced documents carry `source_type`/`source_url` and are flagged 🔗 in the UI.

**How to use it.** URL field on the Knowledge page → submit → the document appears in the list.

## RAG in conversation

**What it does.** With the **RAG ON** toggle, on every question the most relevant chunks (top-k) are folded into the last user message before it is sent to the model; the sources come back to the client as an SSE `rag_context` frame and appear as **citation chips** under the response.

**How to use it.** Enable the **Knowledge (RAG)** toggle in the sidebar **Funzioni** section; ask questions normally. Clicking a citation deep-links to the exact passage in the document (every chunk stores its `char_start`/`char_end` offsets within the source text, exposed by `GET /v1/knowledge/documents/{id}/source`).

**Per-conversation scoping.** Retrieval can be restricted to specific documents: `document_ids` on `/knowledge/search` and `rag_document_ids` on chat completions.

## Hybrid search and reranking

**What it does.** Retrieval fuses two arms:

1. **lexical** — FTS5 (`kb_chunks_fts`) with bm25 ranking;
2. **vector** — cosine similarity (numpy) over the embeddings;

combined with Reciprocal Rank Fusion. Optionally an **LLM reranker** reorders the candidate pool before context injection, degrading gracefully to the fused order on any error.

**Configuration.**

```env
RAG_HYBRID=true            # hybrid search on/off
RAG_CANDIDATE_POOL=20      # candidate pool size
RAG_RERANK=llm             # enable the LLM reranker (opt-in)
RAG_RERANK_MODEL=groq/llama-3.3-70b-versatile
EMBEDDING_CHAIN=ollama:nomic-embed-text,gemini:text-embedding-004
```

## KB maintenance

- **Chunk preview**: `GET /documents/{id}/chunks` shows how a document was split.
- **Re-embed**: button in the UI (`POST /documents/{id}/reembed`) to redo chunking + embedding from the stored source text — useful after changing `EMBEDDING_CHAIN`.
- **Dedicated vector store**: deferred; for very large corpora the documented upgrade path is `sqlite-vec`.
