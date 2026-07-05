# Base de connaissances et RAG

## Ingestion de documents

**Ce que ça fait.** Téléverse des documents (PDF, TXT, DOCX, Markdown) dans une base de connaissances par profil. Le texte est extrait, découpé en chunks (800 caractères, 120 de chevauchement), vectorisé via la chaîne de repli `EMBEDDING_CHAIN` (défaut : Ollama `nomic-embed-text` → Gemini → Mistral) et stocké en vecteurs BLOB float32 dans SQLite (`kb_documents` / `kb_chunks`).

**Comment l'utiliser.** Page dédiée **Connaissances** (`/knowledge`, **Ressources → Connaissances** dans la barre de navigation, ou le lien *Gérer →* à côté de l'interrupteur RAG dans la barre latérale) : téléversez un ou plusieurs fichiers, parcourez la liste des documents, réindexez ou supprimez ce dont vous n'avez plus besoin. API : `GET/POST/DELETE /v1/knowledge/documents`, `POST /v1/knowledge/search`.

## Ingestion d'URL

**Ce que ça fait.** `POST /v1/knowledge/urls` récupère une page web (extraction plein texte du HTML, même approche que l'outil `read_url`) et l'indexe comme un téléversement. Les documents issus du web portent `source_type`/`source_url` et sont marqués 🔗 dans l'interface.

**Comment l'utiliser.** Champ URL sur la page Connaissances → envoyer → le document apparaît dans la liste.

## RAG en conversation

**Ce que ça fait.** Avec l'interrupteur **RAG ON**, à chaque question les chunks les plus pertinents (top-k) sont intégrés au dernier message utilisateur avant l'envoi au modèle ; les sources reviennent au client via une trame SSE `rag_context` et apparaissent comme **puces de citation** sous la réponse.

**Comment l'utiliser.** Activez l'interrupteur **Knowledge (RAG)** dans la section **Fonctions** de la barre latérale ; posez vos questions normalement. Cliquer une citation ouvre le passage exact du document (chaque chunk stocke ses offsets `char_start`/`char_end` dans le texte source, exposés par `GET /v1/knowledge/documents/{id}/source`).

**Portée par conversation.** La récupération peut être restreinte à des documents précis : `document_ids` sur `/knowledge/search` et `rag_document_ids` sur les chat completions.

**Aussi depuis Telegram.** Les utilisateurs avec un profil associé (`/link`) peuvent ingérer des documents (`/kb`), les gérer (`/kb list|del`) et activer la récupération (`/rag on`) directement depuis le bot — voir [Bot Telegram](telegram.md#knowledge-base-rag).

## Recherche hybride et reranking

**Ce que ça fait.** La récupération fusionne deux branches :

1. **lexicale** — FTS5 (`kb_chunks_fts`) avec classement bm25 ;
2. **vectorielle** — similarité cosinus (numpy) sur les embeddings ;

combinées par Reciprocal Rank Fusion. En option, un **reranker LLM** réordonne le pool de candidats avant l'injection du contexte, avec repli gracieux sur l'ordre fusionné en cas d'erreur.

**Configuration.**

```env
RAG_HYBRID=true            # recherche hybride on/off
RAG_CANDIDATE_POOL=20      # taille du pool de candidats
RAG_RERANK=llm             # active le reranker LLM (opt-in)
RAG_RERANK_MODEL=groq/llama-3.3-70b-versatile
EMBEDDING_CHAIN=ollama:nomic-embed-text,gemini:text-embedding-004
```

## Maintenance de la KB

- **Aperçu des chunks** : `GET /documents/{id}/chunks` montre comment un document a été découpé.
- **Re-embed** : bouton dans l'interface (`POST /documents/{id}/reembed`) pour refaire découpage + embedding depuis le texte source stocké — utile après un changement d'`EMBEDDING_CHAIN`.
- **Magasin vectoriel dédié** : reporté ; pour les très gros corpus la voie de mise à niveau documentée est `sqlite-vec`.
