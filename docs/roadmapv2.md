# Roadmap v2 — Backlog consolidato

**Data:** 2026-08-16
**Base:** HEAD `a8460ce` (branch `refactor`, tag `v3.8.0`)
**Fonti consolidate:** [roadmap.md](roadmap.md) · [roadmap-overview.md](roadmap-overview.md) · [roadmap-workflows.md](roadmap-workflows.md) · [roadmap-analisi.md](roadmap-analisi.md) · [roadmap-fix.md](roadmap-fix.md)

Questo documento raccoglie **tutto e solo ciò che resta da fare**, verificato contro il codice
sorgente al momento della stesura (non copiato dallo stato dichiarato nelle roadmap originali,
che in più punti è disallineato — vedi § 6). Aggiunge una sezione dedicata alla **gestione git**
(branch, tag, release, CI) che nelle roadmap precedenti mancava del tutto.

Legenda gravità: 🔴 critico · 🟠 alto · 🟡 medio · ⚪ basso

---

## Indice

1. [Sicurezza — audit QA non risolto](#1-sicurezza--audit-qa-non-risolto) 🔴
2. [Git, branch, tag e release](#2-git-branch-tag-e-release) 🟠
3. [Debito tecnico architetturale](#3-debito-tecnico-architetturale) 🟠
4. [Roadmap prodotto — fasi aperte](#4-roadmap-prodotto--fasi-aperte) 🟡
5. [Roadmap workflow — residui](#5-roadmap-workflow--residui) 🟡
6. [Igiene della documentazione](#6-igiene-della-documentazione) ⚪
7. [Piano di esecuzione consigliato](#7-piano-di-esecuzione-consigliato)

---

## 1. Sicurezza — audit QA non risolto

L'audit del 2026-07-17 ([roadmap-fix.md](roadmap-fix.md)) ha prodotto 20 finding. **Verificati oggi
uno per uno: 18 su 20 erano ancora aperti**, incluse tutte e 4 le Critical. È il blocco a priorità
più alta dell'intero backlog e ha un rapporto costo/beneficio migliore di qualunque nuova feature.

> **Aggiornamento 2026-08-24:** chiusi i due IDOR Critical **2.1** e **2.2** (vedi tabella);
> restano aperte le due Critical di sandbox/SSRF (1.1, 1.2) e gli IDOR gemelli 2.3 e 3.1.

### 1.1 Critical

| ID | Problema | File | Fix |
|---|---|---|---|
| **1.1** | Bypass del blocco di rete nella sandbox `python_exec`: il monkey-patch di `socket` vive dentro l'interprete figlio, ma `subprocess` non è bloccato → un `subprocess.run([sys.executable, "-c", …])` riparte pulito | `backend/app/tools/code_interpreter.py:99` | `sys.addaudithook` che vieta `subprocess`/`os.fork`/`os.exec*`/`ctypes`, **oppure** `unshare(CLONE_NEWNET)` via `preexec_fn` / container `--network none` |
| **1.2** | SSRF completo nel nodo `http.request` del motore workflow: unica validazione `url.startswith(("http://","https://"))`, nessun `assert_public_url` → metadata cloud, `localhost`, rete interna, con `$secrets` negli header | `backend/app/workflow/nodes/io.py:113-115` (il nodo è migrato qui dal vecchio `workflow_graph_service.py`) | importare `assert_public_url` da `app.tools.extras` e sollevare `ValueError` prima della richiesta |
| ~~**2.1**~~ | ~~IDOR sul link Telegram: i 3 endpoint prendono `profile_id` libero da body/path, **zero** controllo di ownership → unlink/lettura/hijack del profilo altrui conoscendo l'UUID~~ | `backend/app/api/v1/endpoints/telegram_link.py` | ✅ **fatto (2026-08-24)** — `_owned_profile`/`assert_owns_profile` verificano il profilo (path *e* body) contro l'utente autenticato: 404 se inesistente, 403 se altrui, e il codice di link monouso non viene consumato da un tentativo respinto. **Non** si è usato `resolve_profile` come suggerito: quelle route ricevono il profilo dal path e il frontend non manda `X-Profile-ID`, quindi il fallback "primo profilo dell'utente" avrebbe silenziosamente cambiato bersaglio per gli utenti multi-profilo |
| ~~**2.2**~~ | ~~IDOR: `DELETE /v1/knowledge/documents/{doc_id}` cancella documenti (chunk, grafo, vettori) di qualsiasi profilo~~ | `backend/app/api/v1/endpoints/knowledge.py` | ✅ **fatto (2026-08-24)** — `resolve_profile` + `doc.profile_id != pid → 404` |

### 1.2 High

| ID | Problema | File | Fix |
|---|---|---|---|
| **1.3** | Bypass SSRF via redirect: `assert_public_url` valida l'URL iniziale ma i client girano con `follow_redirects=True` in **5 punti** → 302 verso `169.254.169.254` | `tools/extras.py:234,334,404` · `tools/builtin.py:104,148` | `follow_redirects=False` + rivalidare ogni `Location` con `assert_public_url`, max 5 hop |
| **2.3** | Data leak cross-tenant: `list_document_chunks`, `get_document_source` (testo integrale!), `get_document_wiki` senza `resolve_profile` | `knowledge.py:171,179,230` | stesso pattern del 2.2 |
| **2.5** | Nessun rate limit su `/v1/auth/login` e `/refresh`: il limiter dipende da `get_current_user`, quindi per costruzione non può proteggere le route pubbliche → brute force illimitato | `api/v1/router.py` · `dependencies/rate_limit.py` | limiter indipendente per IP+email con lockout progressivo |
| **2.6** | User enumeration via timing: lo short-circuit di `or` salta bcrypt quando l'email non esiste | `endpoints/auth.py:58` | eseguire sempre `verify_password` contro un hash dummy precalcolato |
| **4.1** | Perdita silenziosa di messaggi chat: `appendMessages(...)`/`create(...)` senza handler `error` → il messaggio resta solo in memoria e sparisce al refresh | `frontend/.../chat-page.component.ts:1424-1467` | handler `error` + `NotificationService` + flag "non salvato" con retry |

### 1.3 Medium / Low

| ID | Problema | Stato |
|---|---|---|
| **1.4** | Sandbox senza confinamento filesystem + `$secrets` iniettati in chiaro nell'escape hatch `=py:` | aperto |
| **2.7** | `JWT_SECRET_KEY`/`VAULT_SECRET_KEY` di default producono solo un `logging.warning`: l'app parte lo stesso con un segreto pubblico noto → chiunque forgia un JWT `role: admin` | aperto (`main.py:29-41`) — serve **fail-fast** quando `app_env == "production"` |
| **2.8** | I login falliti non finiscono nell'audit log | aperto |
| **3.1** | `reembed_document` ha `resolve_profile` ma non confronta `doc.profile_id` → re-embedding forzato di documenti altrui, con cambio silenzioso di attribuzione | aperto (`knowledge.py:190-193`) |
| **3.2** | Nessun audit log su `create/enable/disable/delete_trigger` e `rotate_webhook_secret` | aperto |
| **3.3** | Documenti "fantasma" se l'ingest fallisce dopo `create_document` (manca `mark_error` nell'`except`) | aperto |
| **4.3** | Collisione di numerazione "Phase 30" tra roadmap (persistenza) e commenti nel codice (hardening workflow) | aperto |
| **4.4** | `GraphWorkflowExport` disallineato dalla risposta reale (`kind`/`secrets`/`workflow_version` assenti, nessun `response_model`) | aperto |

**Già risolti:** 2.1 e 2.2 (2026-08-24, vedi sopra), 2.4 (pin conversazioni, ora usa
`_assert_owns_conversation`) e 4.2 (versione allineata a 3.8.0 su backend, frontend e CHANGELOG).

> **Nota di metodo:** i quattro IDOR (2.1, 2.2, 2.3, 3.1) hanno la stessa causa radice — route
> profile-scoped che omettono `resolve_profile`. **2.1 e 2.2 sono chiusi**, con 7 test di
> regressione in `backend/tests/test_idor.py` (utente A non può toccare risorse di utente B);
> **2.3 e 3.1 restano aperti** e vanno chiusi con lo stesso pattern, aggiungendo i rispettivi test
> allo stesso file. Resta da fare l'**audit sistematico** di tutte le route `/knowledge`,
> `/conversations`, `/telegram`, `/profiles` per verificare ovunque la presenza del controllo.

---

## 2. Git, branch, tag e release

Stato rilevato:

```
* refactor   a8460ce  [origin/refactor]  ← HEAD, tag v3.8.0
  main       4087ee5  [origin/main]      ← 4 commit indietro, describe: v3.7.0-1-g4087ee5
remote: origin  https://github.com/lordraw77/spice-sibyl.git
47 tag locali; nessuna directory .github/ → nessuna CI
working tree: 4 file modificati non committati (lavoro MCP stdio)
```

### 2.1 ✅ `main` non contiene la release corrente — chiuso il 2026-08-25

`v3.8.0` esiste **solo** su `refactor`. Il branch di default del repo (`origin/HEAD → origin/main`)
è fermo a 4 commit prima e non contiene:

- `d8d3bf3` — pool aiosqlite condiviso (P0 #1 del debito tecnico)
- `26bb0c5` — node families LLM/logic/messaging/state
- `ce83a5a` — node families HITL/custom + bus SSE in-memory
- `a8460ce` — versione 3.8.0 + comandi Telegram `/tool`

**Conseguenza:** chi clona il progetto ottiene una versione priva di tutto il refactoring P0/P1 e
della release taggata. Un tag di release che non è raggiungibile dal branch di default è un
anti-pattern: rende `git describe` sul default branch fuorviante e rompe qualunque build "da main".

**Da fare:** allineare `main` a `refactor` (merge o fast-forward — i branch non sono divergenti:
0 commit su `refactor..main`, quindi il fast-forward è pulito), poi decidere il destino di
`refactor`: se è il branch di lavoro permanente, va documentato; se era temporaneo per il
refactoring architetturale, va chiuso dopo il merge.

**Fatto il 2026-08-25:** il lavoro rimasto nel working tree (fix IDOR, fix MCP stdio, documentazione)
è stato committato su `refactor`, poi `main` è stato allineato in **fast-forward** su `refactor` e
pushato: `origin/main` contiene ora `v3.8.0` e tutto il refactoring P0/P1, e `git describe` sul
branch di default è di nuovo significativo. `refactor` resta il **branch di lavoro permanente** —
si sviluppa lì e si porta su `main` in fast-forward a ogni release; la convenzione va scritta nel
`CONTRIBUTING.md` previsto dalla § 2.4.

### 2.2 ✅ Cinque release documentate ma mai taggate — chiuso il 2026-08-24 (senza taggare)

I tag saltano da `v3.0.0` a `v3.6.0`, e il CHANGELOG documenta release intermedie:

| Versione | CHANGELOG | Tag git | Contenuto (dalle note di progetto) |
|---|---|---|---|
| 3.1.0 | ✅ | ❌ assente | Phase 38 — engine extension (fase 6 workflow) |
| 3.2.0 | ✅ | ❌ assente | Phase 39 — operations & governance (fase 7) |
| 3.3.0 | ✅ | ❌ assente | Phase 40 — advanced editor (fase 8) |
| 3.4.0 | ✅ | ❌ assente | Phase 41 — workflow-as-tool, MCP server (fase 9) |
| 3.5.0 | ❌ assente | ❌ assente | Phase 49 — scheduling/SLA/UX (fase 17) |

**Verifica sulla storia git (2026-08-24) — il presupposto di questa sezione era sbagliato:**

```
git log --oneline v3.0.0..v3.6.0
  a4ed227 2026-07-22 feat: add Phase 50 tests for LLM quality gate and A/B testing
  af5bc07 2026-07-17 feat: update version to 3.0.0 …
git log -L15,15:backend/app/core/config.py
  a4ed227: _DEFAULT_VERSION '3.0.0' → '3.6.0'      ← un solo salto, 78 file, +22.146 righe
```

Fra `v3.0.0` e `v3.6.0` esiste **un unico commit**, `a4ed227`, che è già quello taggato `v3.6.0`.
Le versioni 3.1.0–3.5.0 non sono mai esistite come commit: sono tappe di sviluppo confluite in
quel lump. Di conseguenza:

1. **La sezione `[3.5.0]` non va scritta.** Il suo contenuto (Phase 49) è già dentro `[3.6.0]`,
   insieme alle Phase 42–48 e 50 — non c'è materiale orfano da spostare.
2. **I 5 tag non vanno creati:** punterebbero tutti allo stesso commit di `v3.6.0`. Si applica
   l'opzione onesta già prevista qui — buco dichiarato invece che tag falso: il `CHANGELOG.md` ha
   ora una **"Nota sui tag git"** in testa che spiega quali versioni non hanno tag e perché.
3. Niente `make push-tags`: non c'è nulla di nuovo da pushare.

> Il Makefile ha già `tag`, `next-tag BUMP=major|minor|patch`, `push-tags`, `release` e `publish`:
> la tooling di release esiste, è il **processo** che è stato saltato — dalla `v3.6.0` in poi il
> processo è stato rispettato (3.6.0, 3.7.0, 3.8.0 hanno tutte il loro tag). La vera prevenzione è
> il job di coerenza release della § 2.3, non un tag retroattivo.

### 2.3 🟠 Nessuna CI

Non esiste `.github/workflows/`. Tutto — test, lint, build immagini, coerenza versioni — è manuale.

**Da fare** (minimo utile, un solo workflow):
- `on: [push, pull_request]` → build immagine backend + `pytest` in Docker
  (l'host ha Python troppo vecchio: i test girano già in container, vedi note di progetto).
- Job di coerenza release: verifica che `_DEFAULT_VERSION` (`backend/app/core/config.py:15`),
  `frontend/package.json`, l'ultima sezione del `CHANGELOG.md` e il tag git coincidano — è
  esattamente il finding 4.2 dell'audit, che si previene meglio di come si corregge.
- `on: push tags v*` → build + push immagini con il tag della release.

### 2.4 🟡 Igiene del repository

- **Lavoro non committato:** 4 file modificati (`CHANGELOG.md`, `backend/app/services/mcp_client.py`,
  `backend/tests/test_mcp.py`, `docs/mcp-deployment.md`) — è il fix MCP stdio già descritto in
  `[Unreleased]`. Va chiuso in un commit e portato in una release, non lasciato nel working tree.
- **`.claude/settings.json` è tracciato:** contiene le permission dell'agente. Valutare se è
  intenzionale (config di progetto condivisa) o se va spostato in `settings.local.json`.
- **Nessun `CONTRIBUTING.md` né convenzione di branch documentata:** i messaggi di commit seguono
  di fatto Conventional Commits (`feat:`, …) ma la convenzione non è scritta da nessuna parte.
  Con la CI del punto 2.3 si può anche far generare il CHANGELOG dai commit.
- **Nessuna protezione su `main`:** con la CI in piedi, abilitare required checks lato GitHub.

---

## 3. Debito tecnico architetturale

Da [roadmap-analisi.md](roadmap-analisi.md) § 5. I due P0 sono chiusi; restano P1, P2 e P3.

| Prio | Intervento | Stato verificato | Beneficio |
|---|---|---|---|
| ~~P0~~ | `db/pool.py` unico + `transaction()` | ✅ fatto | — |
| ~~P0~~ | Dispatch table dei nodi (`app/workflow/registry.py`) | ✅ fatto | — |
| **P1** | Esplodere `workflow_graph_service.py` | 🟡 **parziale**: 5.555 → **3.919 righe**. Tutte le famiglie di nodi sono estratte in `app/workflow/nodes/*`; il core (`_execute`, checkpoint, scheduler) resta inline per scelta | Manutenibilità, test |
| **P1** | **Segmentare `graph_workflows.py`** in sub-`APIRouter` per risorsa | ❌ **da fare** — ancora **2.102 righe, 83 endpoint** in un file: CRUD, run, trigger, schedule, versioni, approvals, import/export, MCP, git-sync | Manutenibilità, meno merge-conflict |
| **P1** | **Migrazioni versionate** | ❌ **da fare** — nessuna directory di migrazioni, `_SCHEMA` come stringa unica + `_migrate_*` a mano in `db/database.py` (1.184 righe) | Deploy sicuri. **Prerequisito naturale della Phase 37** (§ 4.2): conviene farlo *dentro* quella fase con Alembic, non due volte |
| **P2** | **Esplodere `telegram/bot.py`** | ❌ **da fare** — **2.523 righe, 84 funzioni**: routing comandi, streaming reply, upload, i18n, linking, launcher workflow. Secondo god object del progetto | Test isolati |
| **P2** | **Esplodere `graph_workflow_repository.py`** per aggregato | ❌ **da fare** — **2.400 righe**, un repository unico per workflow/run/trigger/schedule/state/approvals/dedup | Manutenibilità |
| **P2** | `EventBus`/rate-limit/scheduler dietro interfaccia + leader election | 🟡 il bus SSE esiste (`app/workflow/bus.py`) ma è **in-memory**; rate limit e scheduler restano stato di processo → **impossibile scalare a più istanze** | Multi-istanza reale |
| **P2** | Refactor mega-componenti Angular + i18n a sorgente unica | ❌ da fare | Velocità frontend |
| **P3** | Valutare PostgreSQL quando il writer SQLite diventa il collo di bottiglia | ❌ da fare — **coincide con la Phase 37** (§ 4.2) | Scalabilità |

**Regole d'ingaggio (invariate):** funzionalità invariata, un'estrazione per volta, suite verde prima
e dopo ogni step.

---

## 4. Roadmap prodotto — fasi aperte

Delle 30 fasi di [roadmap.md](roadmap.md), ne restano **due**, entrambe mai iniziate.

### 4.1 Phase 25 — Programmatic access (Personal API keys) 🟡

Verificato: nessuna tabella `api_tokens`, nessuna occorrenza di `sk-sibyl` nel codice.

- **25.a — Token personali** — chiavi long-lived `sk-sibyl-…`, salvate **solo come hash** (sha256,
  stesso pattern del `jti` di `refresh_tokens`) in una tabella `api_tokens`
  (`id`, `user_id`, `name`, `token_hash`, `prefix`, `scopes`, `last_used_at`, `expires_at`,
  `revoked`, `created_at`); plaintext mostrato **una sola volta**. `get_current_user` esteso ad
  accettare `Authorization: Bearer sk-sibyl-…` impostando `request.state.user_id` come per il JWT,
  così il rate limiter per utente continua a funzionare. Scope: `chat`, `embeddings`, `read`.
  Endpoint `GET/POST/DELETE /v1/auth/tokens`, tutti auditati.
- **25.b — UI di gestione** — pannello "API keys" per creare/nominare/revocare e copiare il valore
  una volta, con indicatore di ultimo uso.
- **25.c — Documentazione** — esempi `curl` + snippet SDK OpenAI (`base_url=<host>/api/v1`), nuovo
  doc + voce nell'indice del README.

> **Sinergia:** la 25.a tocca esattamente `get_current_user` e il rate limiter, cioè gli stessi due
> punti del finding di sicurezza **2.5** (nessun limite su `/auth/login`). Farli nello stesso
> intervento evita di toccare due volte il layer di autenticazione.

### 4.2 Phase 37 — Pluggable persistence (SQLAlchemy Core) + Data Access Service 🟠

Verificato: nessun `app/db/engine.py`, nessun Alembic, 24 repository ancora su `aiosqlite` raw con
~472 placeholder `?` SQLite-specifici. È la fase **più grande e più rischiosa** del backlog: va
affrontata solo con suite verde e dopo aver chiuso i punti di § 1.

- **37.a — Facade di persistenza + migrazione a SQLAlchemy Core (parità SQLite, zero cambi di
  comportamento).** `app/db/engine.py` costruisce un `AsyncEngine` da `DATABASE_URL`
  (default `sqlite+aiosqlite:///…`, retrocompatibile con `db_path`). `get_db()` restituisce un
  wrapper `Db` (protocollo `execute`/`fetchone`/`fetchall`/`commit`) invece di una
  `aiosqlite.Connection`: le firme dei 24 repository cambiano **meccanicamente, non
  strutturalmente**. `_SCHEMA` diventa `MetaData`/`Table` dialect-neutral. **La suite esistente deve
  passare invariata su SQLite.**
- **37.b — Alembic + schema dialect-aware** — sostituisce `_migrate_*` e il bootstrap
  `CREATE TABLE IF NOT EXISTS`; migrazione baseline, file SQLite esistenti stampati alla baseline al
  primo boot (idempotente). **Assorbe il P1 "migrazioni versionate"** di § 3.
- **37.c — Driver: PostgreSQL, MariaDB, Oracle** — `asyncpg`, `asyncmy`/`aiomysql`,
  `python-oracledb`, scelti dallo schema di `DATABASE_URL`. `PRAGMA journal_mode=WAL` e seeding
  admin dietro un check di dialetto. Profili compose `--profile postgres|mariadb|oracle` che
  rieseguono **la stessa** suite in Docker.
- **37.d — Retrieval store sempre su sqlite-vec/fts5** — vettori e full-text **non** seguono il
  motore relazionale: restano su un file SQLite dedicato affiancato al primario
  (`RETRIEVAL_DB_URL`). I metadati `kb_documents` + markdown canonico migrano invece con il
  relazionale. Zero riscrittura del retrieval, nessuna regressione ANN.
- **37.e — Data Access Service parallelo (topologia opt-in)** — la facade incapsulata in un processo
  `data-access-service` con Dockerfile e servizio compose propri.
  `PERSISTENCE_MODE=embedded|service`: `embedded` (default) = comportamento odierno a latenza zero;
  `service` sostituisce il wrapper `Db` con un client remoto — grazie a 37.a è uno **swap di
  trasporto, non un rewrite**.
- **37.f — Tooling di migrazione dati, config e docs** — `sibyl-db migrate --from … --to …`
  (export/import delle tabelle relazionali in ordine FK, lasciando intatto il KB store SQLite:
  nessun re-embed). Superficie di config: `DATABASE_URL`, `RETRIEVAL_DB_URL`, `PERSISTENCE_MODE`,
  `DATA_SERVICE_URL`, pool knob; `/info` e `/health` riportano motore attivo e statistiche pool;
  `docs/persistence.md` + voce README + label in cinque lingue.

---

## 5. Roadmap workflow — residui

Le fasi **1–20** di [roadmap-workflows.md](roadmap-workflows.md) sono tutte implementate. Restano
tre code, tutte piccole ma tutte con impatto reale a runtime.

### 5.1 🟡 15.5 — Nodi multimodali (mai implementati)

Marcati "⬜ deferred" nella roadmap; verificato: **nessuna occorrenza nel codice**.

- `audio.transcribe` — file audio dallo storage workspace → testo via provider layer/Whisper,
  output `{text, segments}`. **Blocca il resto:** richiede prima la trascrizione nel provider layer.
- `image.ocr` — immagine → testo.
- `image.generate` e `tts` dove il provider layer li supporta, con output scritti nello storage
  workspace (leggibili dai nodi `file.*` della 4.2).

### 5.2 🟠 15.3 — `browser` (Playwright) non funziona in produzione

Il nodo è implementato ma **Playwright non è nell'immagine backend**: a runtime fallisce.
Da fare: aggiungere Playwright + i browser al `Dockerfile` (attenzione al peso dell'immagine —
valutare un'immagine separata per il runner remoto della fase 14 invece di gonfiare il backend) e
un test di smoke che verifichi la disponibilità del binario.

### 5.3 🟠 13.3 — Git sync richiede il rebuild dell'immagine

Il `Dockerfile` è già stato aggiornato per includere `git`, **ma l'immagine non è mai stata
ricostruita**: la funzione di sync delle versioni via subprocess fallisce sui deployment esistenti.
Stessa situazione, già annotata altrove, per **`markitdown` + `sqlite-vec` della Phase 28**: senza
rebuild la KB degrada al fallback numpy con scan O(n).

> **Azione unica:** un solo rebuild + push dell'immagine backend chiude 5.2, 5.3 e il degrado KB.
> È il singolo intervento con il miglior rapporto sforzo/beneficio di tutto il documento, ed è
> naturale abbinarlo alla CI del § 2.3.

### 5.4 ⚪ Suite di test

Note di progetto riportano **~504 test verdi con 5 failure pre-esistenti o flaky** (MCP stdio,
phase26). Vanno triagiati e o corretti o marcati `xfail` con motivazione: una suite con failure
tollerate rende inutile qualunque required check in CI (§ 2.3).

---

## 6. Igiene della documentazione

Le roadmap originali sono in più punti disallineate dalla realtà. Da correggere (o dichiarare
questo documento come unica fonte di verità, deprecando gli altri):

| File | Problema |
|---|---|
| [roadmap-overview.md](roadmap-overview.md) | Segna 📋 le fasi 27, 29 e 30 che sono completate; **non elenca affatto la Phase 37**, che è una delle due realmente aperte |
| [roadmap-workflows.md](roadmap-workflows.md) righe 566-575 | La tabella riassuntiva finale segna ⬜ le fasi **8, 10, 11, 13, 14, 15, 16, 17** mentre le sezioni corrispondenti dello stesso file le danno ✅ COMPLETED. Contraddizione interna |
| [roadmap.md](roadmap.md) righe 280-288 | La sezione "Next" dichiara le fasi workflow 15–19 come backlog aperto: sono tutte chiuse. Anche il "recommended next sprint" finale (7.1 + 7.5 + 8.1) è superato |
| [roadmap.md](roadmap.md) riga 225 vs codice | Collisione "Phase 30": la roadmap la assegna alla persistenza (poi rinumerata 37), il codice usa la stessa etichetta per l'hardening workflow (finding 4.3) |
| [CHANGELOG.md](../CHANGELOG.md) | Manca la sezione `[3.5.0]`; `[Unreleased]` contiene lavoro non committato |

---

## 7. Piano di esecuzione consigliato

Ordinato per rischio-rimosso per unità di sforzo, non per appetibilità.

### Sprint 1 — Mettere in sicurezza e allineare (giorni, non settimane)

1. **Fix di sicurezza Critical + High** (§ 1.1, § 1.2) — i 4 IDOR condividono la stessa fix
   meccanica; SSRF e sandbox sono indipendenti e parallelizzabili. Un test di regressione per
   finding.
2. **Allineare `main` a `refactor`** (§ 2.1) — fast-forward pulito, nessun conflitto atteso.
3. **Rebuild + push dell'immagine backend** (§ 5.3) — sblocca Playwright, git sync e la KB
   sqlite-vec in un colpo solo.
4. **Committare il lavoro MCP pendente** e rilasciarlo (§ 2.4).

### Sprint 2 — Rendere il processo ripetibile

5. **CI minima** (§ 2.3): test in Docker + check di coerenza delle versioni.
6. **Tag mancanti + `[3.5.0]` nel CHANGELOG** (§ 2.2).
7. **Triage dei 5 test rossi** (§ 5.4) — precondizione perché la CI abbia senso.
8. **Allineare le roadmap obsolete** (§ 6).

### Sprint 3 — Debito tecnico P1

9. **Segmentare `graph_workflows.py`** (§ 3) — 83 endpoint in sub-router per risorsa. Basso
   rischio, alto ritorno sulla velocità di sviluppo futura.
10. *(Le migrazioni versionate non si fanno qui: confluiscono nella 37.b.)*

### Sprint 4 — Feature

11. **Phase 25 — API keys** (§ 4.1), abbinata al fix 2.5 (rate limit su login): stesso layer, un
    solo intervento.
12. **15.5 — nodi multimodali** (§ 5.1), a partire dalla trascrizione nel provider layer.

### Sprint 5+ — Il lift grosso

13. **Phase 37** (§ 4.2), rigorosamente nell'ordine a → b → c → d → e → f, con la suite verde come
    cancello a ogni sotto-fase. Assorbe il P1 migrazioni e apre la strada al P3 PostgreSQL.
14. **P2 del debito** (§ 3): split di `telegram/bot.py` e `graph_workflow_repository.py`,
    EventBus/scheduler dietro interfaccia con leader election (prerequisito per il multi-istanza
    reale), refactor dei mega-componenti Angular.

---

## Riepilogo quantitativo

| Area | Aperti | Peso |
|---|---|---|
| Sicurezza (audit QA) | 18 finding, di cui 4 Critical | 🔴 |
| Git / release / CI | 4 aree (main disallineato, 5 tag mancanti, nessuna CI, igiene repo) | 🟠 |
| Debito tecnico | 6 voci P1/P2 + 1 P3, ~7.000 righe in 3 god object residui | 🟠 |
| Roadmap prodotto | 2 fasi (25, 37 — quest'ultima in 6 sotto-fasi) | 🟡 |
| Roadmap workflow | 3 residui (multimodale, immagine Docker ×2) + triage test | 🟡 |
| Documentazione | 5 file disallineati | ⚪ |
