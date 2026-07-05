# Wissensdatenbank und RAG

## Dokumenten-Ingestion

**Was es macht.** Lädt Dokumente (PDF, TXT, DOCX, Markdown) in eine Wissensdatenbank pro Profil hoch. Der Text wird extrahiert, in Chunks zerlegt (800 Zeichen, 120 Überlappung), über die Fallback-Kette `EMBEDDING_CHAIN` eingebettet (Standard: Ollama `nomic-embed-text` → Gemini → Mistral) und als float32-BLOB-Vektoren in SQLite gespeichert (`kb_documents` / `kb_chunks`).

**So wird es benutzt.** Eigene Seite **Wissen** (`/knowledge`, **Ressourcen → Wissen** in der Navbar, oder der Link *Verwalten →* neben dem RAG-Schalter in der Seitenleiste): lade eine oder mehrere Dateien hoch, durchstöbere die Dokumentliste, indexiere neu oder lösche, was du nicht mehr brauchst. API: `GET/POST/DELETE /v1/knowledge/documents`, `POST /v1/knowledge/search`.

## URL-Ingestion

**Was es macht.** `POST /v1/knowledge/urls` ruft eine Webseite ab (Volltext-HTML-Extraktion, derselbe Ansatz wie das Tool `read_url`) und indexiert sie wie einen Upload. Web-Dokumente tragen `source_type`/`source_url` und sind in der UI mit 🔗 markiert.

**So wird es benutzt.** URL-Feld auf der Wissens-Seite → absenden → das Dokument erscheint in der Liste.

## RAG im Gespräch

**Was es macht.** Mit dem Schalter **RAG ON** werden bei jeder Frage die relevantesten Chunks (Top-k) in die letzte Benutzernachricht eingefügt, bevor sie ans Modell geht; die Quellen kommen als SSE-Frame `rag_context` zurück und erscheinen als **Zitat-Chips** unter der Antwort.

**So wird es benutzt.** Aktiviere den Schalter **Knowledge (RAG)** im Bereich **Funktionen** der Seitenleiste; stelle Fragen wie gewohnt. Ein Klick auf ein Zitat springt zur exakten Passage im Dokument (jeder Chunk speichert seine `char_start`/`char_end`-Offsets im Quelltext, verfügbar über `GET /v1/knowledge/documents/{id}/source`).

**Eingrenzung pro Unterhaltung.** Die Suche kann auf bestimmte Dokumente beschränkt werden: `document_ids` bei `/knowledge/search` und `rag_document_ids` bei Chat-Completions.

**Auch von Telegram.** Benutzer mit verknüpftem Profil (`/link`) können Dokumente aufnehmen (`/kb`), verwalten (`/kb list|del`) und die Suche aktivieren (`/rag on`) direkt vom Bot aus — siehe [Telegram-Bot](telegram.md#knowledge-base-rag).

## Hybride Suche und Reranking

**Was es macht.** Die Suche fusioniert zwei Zweige:

1. **lexikalisch** — FTS5 (`kb_chunks_fts`) mit bm25-Ranking;
2. **vektoriell** — Kosinus-Ähnlichkeit (numpy) über die Embeddings;

kombiniert per Reciprocal Rank Fusion. Optional ordnet ein **LLM-Reranker** den Kandidatenpool vor der Kontext-Injektion neu, mit sanftem Rückfall auf die fusionierte Reihenfolge bei Fehlern.

**Konfiguration.**

```env
RAG_HYBRID=true            # hybride Suche an/aus
RAG_CANDIDATE_POOL=20      # Größe des Kandidatenpools
RAG_RERANK=llm             # LLM-Reranker aktivieren (opt-in)
RAG_RERANK_MODEL=groq/llama-3.3-70b-versatile
EMBEDDING_CHAIN=ollama:nomic-embed-text,gemini:text-embedding-004
```

## KB-Wartung

- **Chunk-Vorschau**: `GET /documents/{id}/chunks` zeigt, wie ein Dokument zerlegt wurde.
- **Re-Embed**: Schaltfläche in der UI (`POST /documents/{id}/reembed`), um Chunking + Embedding aus dem gespeicherten Quelltext zu wiederholen — nützlich nach einer Änderung der `EMBEDDING_CHAIN`.
- **Dedizierter Vektorspeicher**: zurückgestellt; für sehr große Korpora ist `sqlite-vec` der dokumentierte Upgrade-Pfad.
