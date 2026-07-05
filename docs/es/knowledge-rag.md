# Base de conocimiento y RAG

## Ingesta de documentos

**Qué hace.** Sube documentos (PDF, TXT, DOCX, Markdown) a una base de conocimiento por perfil. El texto se extrae, se divide en chunks (800 caracteres, 120 de solape), se vectoriza mediante la cadena de respaldo `EMBEDDING_CHAIN` (por defecto: Ollama `nomic-embed-text` → Gemini → Mistral) y se almacena como vectores BLOB float32 en SQLite (`kb_documents` / `kb_chunks`).

**Cómo se usa.** Página dedicada **Conocimiento** (`/knowledge`, **Recursos → Conocimiento** en la barra de navegación, o el enlace *Gestionar →* junto al interruptor RAG de la barra lateral): sube uno o varios archivos, examina la lista de documentos, reindexa o elimina lo que ya no necesites. API: `GET/POST/DELETE /v1/knowledge/documents`, `POST /v1/knowledge/search`.

## Ingesta de URL

**Qué hace.** `POST /v1/knowledge/urls` recupera una página web (extracción de texto completo del HTML, mismo enfoque que la herramienta `read_url`) y la indexa como una subida. Los documentos de origen web llevan `source_type`/`source_url` y se marcan con 🔗 en la interfaz.

**Cómo se usa.** Campo URL en la página Conocimiento → enviar → el documento aparece en la lista.

## RAG en conversación

**Qué hace.** Con el interruptor **RAG ON**, en cada pregunta los chunks más relevantes (top-k) se incorporan al último mensaje del usuario antes de enviarlo al modelo; las fuentes vuelven al cliente como un frame SSE `rag_context` y aparecen como **chips de cita** bajo la respuesta.

**Cómo se usa.** Activa el interruptor **Knowledge (RAG)** en la sección **Funciones** de la barra lateral; haz preguntas con normalidad. Al hacer clic en una cita se abre el pasaje exacto del documento (cada chunk guarda sus offsets `char_start`/`char_end` dentro del texto fuente, expuestos por `GET /v1/knowledge/documents/{id}/source`).

**Alcance por conversación.** La recuperación puede restringirse a documentos concretos: `document_ids` en `/knowledge/search` y `rag_document_ids` en las chat completions.

**También desde Telegram.** Los usuarios con perfil vinculado (`/link`) pueden ingerir documentos (`/kb`), gestionarlos (`/kb list|del`) y activar la recuperación (`/rag on`) directamente desde el bot — véase [Bot de Telegram](telegram.md#knowledge-base-rag).

## Búsqueda híbrida y reranking

**Qué hace.** La recuperación fusiona dos ramas:

1. **léxica** — FTS5 (`kb_chunks_fts`) con ranking bm25;
2. **vectorial** — similitud coseno (numpy) sobre los embeddings;

combinadas mediante Reciprocal Rank Fusion. Opcionalmente un **reranker LLM** reordena el pool de candidatos antes de inyectar el contexto, degradando con gracia al orden fusionado ante cualquier error.

**Configuración.**

```env
RAG_HYBRID=true            # búsqueda híbrida on/off
RAG_CANDIDATE_POOL=20      # tamaño del pool de candidatos
RAG_RERANK=llm             # activa el reranker LLM (opt-in)
RAG_RERANK_MODEL=groq/llama-3.3-70b-versatile
EMBEDDING_CHAIN=ollama:nomic-embed-text,gemini:text-embedding-004
```

## Mantenimiento de la KB

- **Vista previa de chunks**: `GET /documents/{id}/chunks` muestra cómo se dividió un documento.
- **Re-embed**: botón en la interfaz (`POST /documents/{id}/reembed`) para rehacer división + embedding desde el texto fuente guardado — útil tras cambiar `EMBEDDING_CHAIN`.
- **Almacén vectorial dedicado**: aplazado; para corpus muy grandes la vía de mejora documentada es `sqlite-vec`.
