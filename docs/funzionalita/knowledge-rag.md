# Knowledge base e RAG

## Ingestione documenti

**Cosa fa.** Carica documenti (PDF, TXT, DOCX, Markdown) in una knowledge base per profilo. Il testo viene estratto, suddiviso in chunk (800 caratteri, overlap 120), trasformato in embedding tramite la catena di fallback `EMBEDDING_CHAIN` (default: Ollama `nomic-embed-text` → Gemini → Mistral) e salvato come vettori float32 BLOB in SQLite (`kb_documents` / `kb_chunks`).

**Come si usa.** Pagina dedicata **Knowledge** (`/knowledge`, voce **Risorse → Knowledge** nella navbar, o link *Gestisci →* accanto all'interruttore RAG in sidebar): carica uno o più file, consulta l'elenco dei documenti, re-indicizza o elimina quelli non più utili. API: `GET/POST/DELETE /v1/knowledge/documents`, `POST /v1/knowledge/search`.

## Ingestione da URL

**Cosa fa.** `POST /v1/knowledge/urls` scarica una pagina web (estrazione full-text dell'HTML, stesso approccio del tool `read_url`) e la indicizza come un upload. I documenti da web portano `source_type`/`source_url` e sono contrassegnati 🔗 nella UI.

**Come si usa.** Campo URL nella pagina Knowledge → invio → il documento compare in lista.

## RAG in conversazione

**Cosa fa.** Con il toggle **RAG ON**, a ogni domanda i chunk più pertinenti (top-k) vengono ripiegati nell'ultimo messaggio utente prima dell'invio al modello; le fonti tornano al client come frame SSE `rag_context` e compaiono come **chip di citazione** sotto la risposta.

**Come si usa.** Attiva il toggle **Knowledge (RAG)** nella sezione **Funzioni** della sidebar; fai domande normalmente. Cliccando una citazione si risale al passaggio esatto del documento (ogni chunk memorizza gli offset `char_start`/`char_end` nel testo sorgente, esposto da `GET /v1/knowledge/documents/{id}/source`).

**Scoping per conversazione.** È possibile limitare il retrieval a documenti specifici: `document_ids` su `/knowledge/search` e `rag_document_ids` sulle chat completions.

## Ricerca ibrida e reranking

**Cosa fa.** Il retrieval fonde due "braccia":

1. **lessicale** — FTS5 (`kb_chunks_fts`) con ranking bm25;
2. **vettoriale** — similarità coseno (numpy) sugli embedding;

combinate con Reciprocal Rank Fusion. Opzionale un **reranker LLM** che riordina il pool di candidati prima dell'iniezione nel contesto, con degradazione elegante all'ordine fuso in caso di errore.

**Configurazione.**

```env
RAG_HYBRID=true            # ricerca ibrida on/off
RAG_CANDIDATE_POOL=20      # ampiezza del pool candidati
RAG_RERANK=llm             # abilita il reranker LLM (opt-in)
RAG_RERANK_MODEL=groq/llama-3.3-70b-versatile
EMBEDDING_CHAIN=ollama:nomic-embed-text,gemini:text-embedding-004
```

## Manutenzione della KB

- **Anteprima chunk**: `GET /documents/{id}/chunks` mostra come è stato suddiviso un documento.
- **Re-embed**: pulsante nella UI (`POST /documents/{id}/reembed`) per rifare chunking + embedding dal testo sorgente salvato — utile dopo aver cambiato `EMBEDDING_CHAIN`.
- **Vector store dedicato**: rimandato; per corpora molto grandi il percorso di upgrade documentato è `sqlite-vec`.
