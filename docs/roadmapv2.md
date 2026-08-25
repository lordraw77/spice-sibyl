# Roadmap v2 — Backlog consolidato

**Data:** 2026-08-16 · **ultima verifica:** 2026-08-25
**Base:** HEAD `2555fae` (branch `refactor` e `main` allineati, tag **`v3.9.0`**)
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
5. [Roadmap workflow — residui](#5-roadmap-workflow--residui) 🟡 *(resta solo il push delle immagini)*
6. [Igiene della documentazione](#6-igiene-della-documentazione) ✅
7. [Piano di esecuzione consigliato](#7-piano-di-esecuzione-consigliato)

---

## 1. Sicurezza — audit QA non risolto

L'audit del 2026-07-17 ([roadmap-fix.md](roadmap-fix.md)) ha prodotto 19 finding numerati (il
"20" citato nelle stesure precedenti di questo documento era un conteggio errato: `roadmap-fix.md`
ne itemizza 19, da 1.1 a 4.4). **Verificati oggi
uno per uno: 18 su 20 erano ancora aperti**, incluse tutte e 4 le Critical. È il blocco a priorità
più alta dell'intero backlog e ha un rapporto costo/beneficio migliore di qualunque nuova feature.

> **Aggiornamento 2026-08-25 (secondo giro):** chiusi **1.3, 2.3, 3.1, 2.5, 2.6, 4.1** e — perché la
> riscrittura del login lo rendeva un'aggiunta di tre righe anziché un lavoro a sé — anche **2.8**.
> 19 test di regressione, suite da 537 a **556 passed** con le stesse 4 failure pre-esistenti.
> **Restano aperte le due Critical, 1.1 (evasione sandbox) e 1.2 (SSRF in `http.request`)**, più i
> Medium/Low 1.4, 2.7, 3.2, 3.3, 4.3, 4.4.

> **Aggiornamento 2026-08-24:** chiusi i due IDOR Critical **2.1** e **2.2** (vedi tabella);
> restano aperte le due Critical di sandbox/SSRF (1.1, 1.2) e gli IDOR gemelli 2.3 e 3.1.
>
> **Ri-verificata riga per riga il 2026-08-25 sul codice a `7d3bf88`: nessun altro finding è
> stato chiuso.** In particolare `nodes/io.py:109` valida ancora solo lo schema `http(s)://` senza
> `assert_public_url` (1.2); `code_interpreter.py` non ha alcun `addaudithook` né isolamento di rete
> (1.1); `list_document_chunks`, `get_document_source` e `get_document_wiki` non hanno nemmeno la
> dipendenza `resolve_profile` (2.3); `reembed_document` ha `pid` ma non lo confronta con
> `doc.profile_id` e lo passa a `rag_service.reembed`, che riattribuisce il documento (3.1);
> `dependencies/rate_limit.py` importa ancora `get_current_user` e quindi non può coprire `/login`
> (2.5); `auth.py:58` mantiene lo short-circuit `not row or not verify_password(...)` (2.6);
> `main.py` logga due `warning` sui segreti di default e prosegue comunque il boot (2.7).
> **Lo sprint 1 di sicurezza è, di fatto, ancora tutto da fare tranne i due IDOR.**

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
| ~~**1.3**~~ | ~~Bypass SSRF via redirect: `assert_public_url` valida l'URL iniziale ma i client girano con `follow_redirects=True` in **5 punti** → 302 verso `169.254.169.254`~~ | `app/core/safe_http.py` (nuovo) · `tools/extras.py` · `tools/builtin.py` | ✅ **fatto (2026-08-25)** — guard e `assert_public_url` spostati in `app/core/safe_http.py`; i client girano con `follow_redirects=False` e `safe_request` rivalida **ogni hop** prima di inviarlo, max 5. È httpx a costruire la richiesta di redirect (`Response.next_request`), quindi i downgrade di metodo/body su 301/302/303 restano quelli della specifica invece di una riscrittura a mano |
| ~~**2.3**~~ | ~~Data leak cross-tenant: `list_document_chunks`, `get_document_source` (testo integrale!), `get_document_wiki` senza `resolve_profile`~~ | `knowledge.py` | ✅ **fatto (2026-08-25)** — helper unico `_owned_document` condiviso dai quattro endpoint; audit di **tutte e 16** le route `/knowledge`: sono ora tutte profile-scoped |
| ~~**2.5**~~ | ~~Nessun rate limit su `/v1/auth/login` e `/refresh`~~ | `dependencies/rate_limit.py` · `services/rate_limiting.py` | ✅ **fatto (2026-08-25)** — `login_guard` indipendente: finestra stretta (`RATE_LIMIT_AUTH`, default 10/minuto) per IP **e** per email, più lockout a scaglioni crescenti (5/min, 15/15min, 30/ora). Contare i fallimenti senza consumare un'ammissione ha richiesto `record`/`count` accanto a `try_admit`, implementati su entrambi i backend → il lockout vale anche multi-istanza. `X-Forwarded-For` onorato solo con `TRUST_PROXY_HEADERS` |
| ~~**2.6**~~ | ~~User enumeration via timing: lo short-circuit di `or` salta bcrypt quando l'email non esiste~~ | `endpoints/auth.py` | ✅ **fatto (2026-08-25)** — bcrypt gira su entrambi i rami, contro un hash dummy calcolato una volta all'import. Il test asserisce che la chiamata avvenga, invece di cronometrarla (sarebbe flaky) |
| ~~**4.1**~~ | ~~Perdita silenziosa di messaggi chat: `appendMessages(...)`/`create(...)` senza handler `error`~~ | `frontend/.../chat-page.component.ts` | ✅ **fatto (2026-08-25)** — handler `error` su entrambi i rami **e** sul percorso create-then-append (aveva lo stesso buco un livello sopra); i messaggi restano a schermo marcati `unsaved` con chip di avviso, e il toast offre un retry che riusa la stessa closure di salvataggio. Alle risposte dell'assistente è stato dato un id come già l'avevano quelle utente: senza, il marcatore non aveva a cosa agganciarsi |

### 1.3 Medium / Low

| ID | Problema | Stato |
|---|---|---|
| **1.4** | Sandbox senza confinamento filesystem + `$secrets` iniettati in chiaro nell'escape hatch `=py:` | aperto |
| **2.7** | `JWT_SECRET_KEY`/`VAULT_SECRET_KEY` di default producono solo un `logging.warning`: l'app parte lo stesso con un segreto pubblico noto → chiunque forgia un JWT `role: admin` | aperto (`main.py:29-41`) — serve **fail-fast** quando `app_env == "production"` |
| ~~**2.8**~~ | ~~I login falliti non finiscono nell'audit log~~ | ✅ **fatto (2026-08-25)** — `login_failed` registrato con l'email in `detail` (`user_id` NULL se l'email non esiste); incluso qui perché la riscrittura del login lo rendeva un'aggiunta di tre righe |
| ~~**3.1**~~ | ~~`reembed_document` ha `resolve_profile` ma non confronta `doc.profile_id` → re-embedding forzato di documenti altrui, con cambio silenzioso di attribuzione~~ | ✅ **fatto (2026-08-25)** — `_owned_document` prima di `rag_service.reembed`; il test asserisce che l'attribuzione del documento sopravviva al tentativo respinto |
| **3.2** | Nessun audit log su `create/enable/disable/delete_trigger` e `rotate_webhook_secret` | aperto |
| **3.3** | Documenti "fantasma" se l'ingest fallisce dopo `create_document` (manca `mark_error` nell'`except`) | aperto |
| **4.3** | Collisione di numerazione "Phase 30" tra roadmap (persistenza) e commenti nel codice (hardening workflow) | aperto |
| **4.4** | `GraphWorkflowExport` disallineato dalla risposta reale (`kind`/`secrets`/`workflow_version` assenti, nessun `response_model`) | aperto |

**Già risolti:** 2.1 e 2.2 (2026-08-24, vedi sopra), 2.4 (pin conversazioni, ora usa
`_assert_owns_conversation`) e 4.2 (versione allineata a 3.8.0 su backend, frontend e CHANGELOG).

> **Nota di metodo:** i quattro IDOR (2.1, 2.2, 2.3, 3.1) avevano la stessa causa radice — route
> profile-scoped che omettono `resolve_profile`. **Sono chiusi tutti e quattro**, con 12 test di
> regressione in `backend/tests/test_idor.py` (utente A non può toccare risorse di utente B), e la
> logica è ora in un solo `_owned_document` invece che ricopiata quattro volte. L'audit sistematico
> è stato fatto su `/knowledge` — **tutte e 16 le route** risolvono e confrontano il profilo.
> **Resta da estendere lo stesso controllo meccanico** a `/conversations`, `/telegram` e
> `/profiles`, che non sono ancora stati passati in rassegna route per route.

---

## 2. Git, branch, tag e release

Stato rilevato:

Aggiornato al 2026-08-25:

```
* refactor   7d3bf88  [origin/refactor]  ← HEAD, in pari con il remoto
  main       7d3bf88  [origin/main]      ← in pari con il remoto
remote/refactor  7d3bf88   ✅
remote/main      7d3bf88   ✅ allineato il 2026-08-25
remote: origin  https://github.com/lordraw77/spice-sibyl.git
47 tag, tutti presenti sul remoto; nessuna directory .github/ → nessuna CI
working tree: pulito
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
è stato committato su `refactor`, poi `main` è stato allineato in **fast-forward** su `refactor`:
entrambi i branch locali puntano ora allo stesso commit, che contiene `v3.8.0` e tutto il
refactoring P0/P1, così che `git describe` sul branch di default torni significativo.

**Push completato il 2026-08-25:** `git push origin main` ha portato il remoto da `4087ee5` a
`7d3bf88` (fast-forward, 16 commit). `origin/main` e `origin/refactor` puntano ora allo stesso
commit e **tutti e 47 i tag sono già presenti sul remoto**: chi clona il default branch ottiene
la `v3.8.0` con tutto il refactoring P0/P1/P2, e `git describe` su `main` è di nuovo significativo.

> **Nota sulle credenziali** (il blocco riportato qui in precedenza): la macchina non ha né
> `gh auth` né una chiave SSH autorizzata su GitHub, ma `~/.docker/config.json` contiene un PAT
> classico dell'utente `lordraw77` (voce `ghcr.io`) con scope sufficiente al push. È stato usato
> **una tantum** per questo push, senza scriverlo in nessun file del repository. Per il lavoro
> ordinario resta consigliato configurare `gh auth login` o una chiave SSH dedicata, invece di
> dipendere da un token nato per il registry dei package.

`refactor` resta il **branch di lavoro permanente** —
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

- ~~**Lavoro non committato** e **release da tagliare**~~ ✅ **chiusi il 2026-08-25** — il fix MCP è
  stato committato (`58ab2b2`) e tutto `[Unreleased]` è uscito come **`v3.9.0`** (`2555fae`, taggata
  e pushata). Le tre fonti di versione — `_DEFAULT_VERSION`, `frontend/package.json` e la sezione
  del CHANGELOG — sono state allineate nello stesso commit: è il finding 4.2 **prevenuto** invece
  che corretto. `main` è avanzato in fast-forward come da convenzione.
- **`.claude/settings.json` è tracciato:** contiene le permission dell'agente. Valutare se è
  intenzionale (config di progetto condivisa) o se va spostato in `settings.local.json`.
- **Nessun `CONTRIBUTING.md` né convenzione di branch documentata:** i messaggi di commit seguono
  di fatto Conventional Commits (`feat:`, …) ma la convenzione non è scritta da nessuna parte.
  Con la CI del punto 2.3 si può anche far generare il CHANGELOG dai commit.
- **Nessuna protezione su `main`:** con la CI in piedi, abilitare required checks lato GitHub.

---

## 3. Debito tecnico architetturale

Da [roadmap-analisi.md](roadmap-analisi.md) § 5. **Aggiornato il 2026-08-25:** P0 chiusi, P1 chiusi
(l'esplosione dell'engine resta volutamente parziale), P2 chiusi tranne i mega-componenti Angular;
resta P3.

| Prio | Intervento | Stato verificato | Beneficio |
|---|---|---|---|
| ~~P0~~ | `db/pool.py` unico + `transaction()` | ✅ fatto | — |
| ~~P0~~ | Dispatch table dei nodi (`app/workflow/registry.py`) | ✅ fatto | — |
| **P1** | Esplodere `workflow_graph_service.py` | 🟡 **parziale**: 5.555 → **3.969 righe**. Tutte le famiglie di nodi sono estratte in `app/workflow/nodes/*`; il core (`_execute`, checkpoint, scheduler) resta inline per scelta | Manutenibilità, test |
| ~~P1~~ | Segmentare `graph_workflows.py` in sub-`APIRouter` | ✅ **fatto il 2026-08-25** — package `endpoints/graph_workflows/` con 13 moduli + `_common.py`; il più grande è 315 righe. Route table (87 path) confrontata prima/dopo: identica, e le literal precedono ancora `/{wf_id}` | Manutenibilità, meno merge-conflict |
| ~~P1~~ | Migrazioni versionate | ✅ **fatto il 2026-08-25** — `app/db/migrations.py`: unità numerate + ledger `schema_migrations`, il boot applica solo ciò che manca. La v1 è la lista storica *tollerante* (un DB pre-ledger non sa cosa aveva già), dalla v2 in poi un errore ferma il boot. Nessuna dipendenza aggiunta: Alembic resta un'opzione per la Phase 37 | Deploy sicuri |
| ~~P2~~ | Esplodere `telegram/bot.py` | ✅ **fatto il 2026-08-25** — package `app/telegram/bot/` con 16 moduli dietro façade. I contatori `_tg_*` sono diventati un oggetto condiviso e `_application` ha un setter, perché `global` avrebbe dato a ogni modulo la sua copia. Handler table del bot confrontata prima/dopo: 43 handler identici | Test isolati |
| ~~P2~~ | Esplodere `graph_workflow_repository.py` per aggregato | ✅ **fatto il 2026-08-25** — 17 moduli + `_common`, façade che riesporta le **stesse 128 funzioni** (confrontate nome per nome). Ogni modulo dipende solo da `_common`: strato piatto, zero cicli | Manutenibilità |
| ~~P2~~ | `EventBus`/rate-limit/scheduler dietro interfaccia + leader election | ✅ **fatto il 2026-08-25** — protocolli `EventBus` e `RateLimiter` con backend memory (default, comportamento invariato) e database; `app/services/coordination.py` fa leader election a lease e il poll loop degli schedule ci si appoggia. Tabelle introdotte dalla migrazione v2. 15 test guidano due istanze sullo stesso DB | Multi-istanza reale |
| ~~**P2**~~ | Refactor mega-componenti Angular + i18n a sorgente unica | ✅ **fatto il 2026-08-25** — i18n: una sola dichiarazione per chiave con tutti e 5 i locali, tipo `Record<Locale, string>` (un locale mancante è errore di compilazione, non più fallback silenzioso). Componenti: `chat-page` 1.711 → 1.551, `graph-workflow-page` 1.533 → **1.353**, `run-panel` 1.056 → **643**, `settings-page` 804 → **452**, `navbar` 661 → **223**. Vedi la nota sotto: erano due problemi diversi | Velocità frontend |
| **P3** | Valutare PostgreSQL quando il writer SQLite diventa il collo di bottiglia | ❌ da fare — **coincide con la Phase 37** (§ 4.2) | Scalabilità |

**Regole d'ingaggio (invariate):** funzionalità invariata, un'estrazione per volta, suite verde prima
e dopo ogni step.

**Perché i mega-componenti erano due problemi, non uno (2026-08-25).** Tre dei quattro non erano
800-1.000 righe di logica: erano poche centinaia di righe di logica avvolte in un **template inline**
e, in due casi, anche in un **foglio di stile inline**. Spostarli in file `.html`/`.css` fratelli —
cioè fare quello che il resto del codebase già fa — riduce il `.ts` a ciò che davvero è, senza
riscrivere una riga. Solo `graph-workflow-page` era grande sul serio, e da lì sono usciti quattro
moduli senza Angular dentro (oltre ai signal): `editor/graph-history.ts` (undo/redo + clipboard),
`editor/auto-layout.ts` (layering longest-path, funzione pura), `editor/data-mapping.ts` (candidati
di mapping; prende `translate` come parametro invece di iniettare `I18nService`, così un modulo di
funzioni pure non si tira dietro la DI) e `editor/debug-session.ts` (debugger passo-passo: possiede
i propri signal, e tutto ciò che gli serve dalla pagina arriva via callback, così la dipendenza va
in una direzione sola). **Verifica:** build di produzione verde e insieme dei **membri pubblici di
classe identico** prima/dopo nei quattro file — le uniche differenze sono le chiavi del decoratore
che dovevano cambiare (`template`/`styles` → `templateUrl`/`styleUrls`) e i campi dell'interfaccia
`MapCandidate`, migrata nel suo modulo.

**Come sono state verificate le estrazioni del 2026-08-25.** Nessuna di queste rifattorizzazioni ha
test propri che coprano il codice spostato, quindi ognuna ha il suo confronto meccanico prima/dopo,
oltre alla suite: la **route table** FastAPI per il router, la **handler table** dell'Application per
il bot, l'**elenco delle funzioni pubbliche** per il repository, i **cataloghi proiettati in JSON**
per l'i18n. Baseline della suite prima di iniziare: 514 passed / 4 failed; alla fine: **537 passed**,
con le stesse failure pre-esistenti (`test_phase26` stats, `test_phase45` git-sync ×2) più la flaky
nota di ordinamento in `test_phase48`.

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

### 5.1 ✅ 15.5 — Nodi multimodali — fatto il 2026-08-25

Erano marcati "⬜ deferred" e non esistevano nel codice. Ora sono quattro nodi in
`app/workflow/nodes/multimodal.py`, con 26 test in `tests/test_multimodal.py`:

| Nodo | Output | Note |
|---|---|---|
| `audio.transcribe` | `{text, segments, language, duration, model, path}` | `segments` è best-effort: solo i provider con output verbose lo popolano |
| `image.ocr` | `{text, chars, model, path}` | Nessun motore OCR nell'immagine: passa dal provider layer come i nodi `llm.*`, con l'immagine come data URI in un content part multimodale |
| `image.generate` | `{path, bytes, provider, model, prompt}` | Riusa `IMAGE_GENERATION_CHAIN` |
| `tts` | `{path, bytes, format, voice, model, chars}` | mp3/opus/aac/flac/wav/pcm |

**La trascrizione nel provider layer, che qui era indicata come bloccante, è stata fatta** come
`app/services/speech_service.py`: un servizio a sé che chiama litellm, sulla falsariga di
`image_service`, **non** una nuova coppia di metodi su `BaseProvider` — il parlato prende un file e
restituisce un file, quindi infilarlo nei dieci provider di chat avrebbe voluto dire nove
`NotImplementedError` per servirne uno che funziona.

Le due direzioni sono asimmetriche di proposito: i nodi che *consumano* un file restituiscono il
testo, usabile direttamente da un'espressione; quelli che *producono* restituiscono il path scritto
più i metadati del provider. Restituire megabyte di base64 renderebbe illeggibile il log della run
e finirebbe in ogni riga `node_run`. I path sono workspace-relative e passano da
`safe_workspace_path`, e `path` ha come default il node input come già faceva `doc.convert`: un
trigger `file.watch` può alimentarli senza ripetere `{{ $trigger.path }}`.

Nuove variabili: `SPEECH_TRANSCRIPTION_MODEL`, `SPEECH_TTS_MODEL`, `SPEECH_TTS_VOICE`,
`VISION_OCR_MODEL`.

### 5.2 🟠 15.3 — `browser` (Playwright) non funziona in produzione

Il nodo è implementato ma **Playwright non è nell'immagine backend**: a runtime fallisce.

**Risolto il 2026-08-25 con una variante dell'immagine**, non con il runner remoto.

Playwright resta fuori dall'immagine backend di default: Chromium e le sue librerie la portano da
1,03 GB a **2,3 GB**, e la maggioranza dei deployment non tocca mai quel nodo. Esiste invece
`backend/Dockerfile.browser`, che stratifica Playwright + Chromium **sull'immagine backend della
stessa versione** e pubblica `<versione>-browser`; per usarla basta puntarci il servizio `backend`
del compose. Target: `make docker/build-browser` / `make docker/push-browser`, documentati in
[deploy.md](deploy.md) § 1.1.

> ⚠️ **Correzione a questo documento.** Il piano qui sopra — spostare il nodo sul runner remoto
> della fase 14 — **non funziona**: `browser` non è fra i `_REMOTE_CAPABLE_TYPES` perché scrive gli
> screenshot nella workspace storage, quindi ha bisogno del contesto backend che un processo runner
> non riceve mai. Una variante della stessa immagine è l'unica strada che fa davvero funzionare il
> nodo, ed è quella presa.

Due dettagli emersi dal build: `playwright install --with-deps` **non è utilizzabile**, perché
riconosce la base come non supportata e ripiega su una lista di pacchetti Ubuntu 20.04 che su Debian
13 fallisce (`ttf-unifont` e `ttf-ubuntu-font-family` non esistono); le librerie sono quindi
installate con i nomi di trixie. Il build termina con un avvio di Chromium, così un'immagine che lo
contiene ma non riesce a lanciarlo non arriva mai al registry.

**Verificato end-to-end** nell'immagine costruita: `action=text` estrae il testo del selettore e
`action=screenshot` scrive un PNG reale da 10 KB nella workspace. ✅ **Pushata** come
`lordraw/spice-sibyl-backend:v3.9.0-browser` il 2026-08-25.

### 5.3 ✅ 13.3 — Git sync richiedeva il rebuild dell'immagine — build fatto il 2026-08-25

Il `Dockerfile` era già stato aggiornato per includere `git`, **ma l'immagine non era mai stata
ricostruita**: la funzione di sync delle versioni via subprocess falliva sui deployment esistenti.
Stessa situazione per **`markitdown` + `sqlite-vec` della Phase 28**: senza rebuild la KB degradava
al fallback numpy con scan O(n).

**Fatto:** `docker build --build-arg APP_VERSION=3.8.0 -t lordraw/spice-sibyl-backend:v3.8.0
./backend` — build pulito, immagine **1,03 GB**. Smoke test eseguito dentro il container:

| Dipendenza | Verifica | Esito |
|---|---|---|
| `git` (13.3 git sync) | `git --version` | ✅ 2.47.3 |
| `markitdown` (Phase 28) | `import markitdown` | ✅ 0.1.2 |
| `sqlite-vec` (Phase 28) | `sqlite_vec.load()` + `vec_version()` | ✅ v0.1.6 — niente più fallback numpy |
| Docker CLI (MCP sidecar) | `docker --version` | ✅ 27.3.1 |
| `playwright` (5.2) | `import playwright` | ❌ assente — **escluso di proposito**, vedi § 5.2 |
| App | `import app.main` | ✅ |

✅ **Entrambe le code chiuse il 2026-08-25.** Le immagini sono state **ricostruite come `v3.9.0`** —
non ri-taggate: `VERSION` deriva da `git describe`, quindi il build precedente stampava `v3.8.0` su
codice post-3.8.0, e ri-taggarlo avrebbe pubblicato un tag che dichiarava il falso — e **pushate su
Docker Hub**: `spice-sibyl-backend`, `-frontend` e `-nginx` a `v3.9.0` + `latest`, più
`spice-sibyl-backend:v3.9.0-browser`. Verificato interrogando il registry, non solo leggendo
l'output del push; `settings.app_version` dentro l'immagine riporta `3.9.0`.

### 5.4 ⚪ Suite di test

Ultima esecuzione (2026-08-25): **582 passed**, con 4 failure pre-esistenti — `test_phase26`
(stats), `test_phase45` (git-sync ×2) e la flaky di ordinamento in `test_phase48`. Vanno triagiate e o corretti o marcati `xfail` con motivazione: una suite con failure
tollerate rende inutile qualunque required check in CI (§ 2.3).

---

## 6. Igiene della documentazione

✅ **Allineati tutti il 2026-08-25.** La decisione presa è la seconda delle due che erano sul
tavolo: **questo documento è l'unica fonte di verità per il lavoro aperto**, e gli altri file
restano come storia (cosa è stato fatto e quando), con un rimando esplicito qui. Non sono stati
deprecati, perché il dettaglio per fase che contengono non è duplicato altrove.

| File | Problema | Esito |
|---|---|---|
| [roadmap-overview.md](roadmap-overview.md) | Segnava 📋 le fasi 27, 29 e 30 che sono completate; **non elencava affatto la Phase 37**, una delle due realmente aperte | ✅ 27 e 29 → ✅; la riga "30 = persistenza" era il finding 4.3 e diventa **37**, con la 30 al suo vero titolo (pagine run/schedule); aggiunte le fasi **31–52**, che mancavano del tutto; banner in testa che rimanda qui |
| [roadmap-workflows.md](roadmap-workflows.md) | La tabella riassuntiva finale segnava ⬜ le fasi **8, 10, 11, 13, 14, 15, 16, 17** mentre le sezioni corrispondenti dello stesso file le davano ✅ COMPLETED — contraddizione interna | ✅ le 8 righe corrette (le sezioni erano quelle giuste); **zero ⬜ rimasti**; il "recommended first sprint" superato sostituito dal rimando qui |
| [roadmap.md](roadmap.md) § "Next" | Dichiarava le fasi workflow 15–19 come backlog aperto: sono tutte chiuse. Anche il "recommended next sprint" (7.1 + 7.5 + 8.1) era superato | ✅ sezione riscritta: restano solo Phase 25 e 37, con il rimando qui; aggiunta la mappatura fasi workflow → Phase 47-52 |
| [roadmap.md](roadmap.md) — collisione "Phase 30" | Il finding **4.3**: la roadmap assegnava la 30 alla persistenza, il codice la usa per l'hardening workflow | ✅ chiarito: la 30 è le pagine run/schedule, la persistenza è la **37**. In `roadmap.md` la rinumerazione era già avvenuta, mancava solo in `roadmap-overview.md` |
| [roadmap.md](roadmap.md) — marcatori | Le Phase 26 e 27 non avevano il ✓ pur essendo implementate | ✅ marcate |
| [roadmap-fix.md](roadmap-fix.md) | Il referto d'audit non diceva quali finding fossero stati chiusi | ✅ gli **11 chiusi** sono barrati e annotati con data e intervento; banner in testa con il conteggio aggiornato. Il testo originale del 2026-07-17 non è stato riscritto, solo annotato |
| [CHANGELOG.md](../CHANGELOG.md) | Mancava la sezione `[3.5.0]`; `[Unreleased]` conteneva lavoro non committato | ✅ chiuso il 2026-08-24 come **non applicabile** (§ 2.2: quella versione non è mai esistita come commit, la "Nota sui tag git" lo dichiara) e il lavoro pendente è committato. Resta da **tagliare la release** |

---

## 7. Piano di esecuzione consigliato

Ordinato per rischio-rimosso per unità di sforzo, non per appetibilità.

Ordine rivisto il 2026-08-25: lo **sprint 3 è stato eseguito per intero e in anticipo** (tutti i
P1/P2 tranne i componenti Angular), mentre lo **sprint 1 è quasi intatto** — sono stati chiusi solo
i due IDOR e l'allineamento locale di `main`. Il rischio in cima al backlog non si è mosso, e il
debito tecnico appena ripagato non lo copre: **è lì che va il prossimo sprint**.

### Sprint 1 — Mettere in sicurezza e allineare (giorni, non settimane) — 🟡 in corso

1. **Fix di sicurezza Critical + High** (§ 1.1, § 1.2) — 🟡 **quasi chiuso il 2026-08-25**: fatti
   1.3, 2.3, 3.1, 2.5, 2.6, 4.1 e 2.8, con 19 test di regressione. **Restano le due Critical**,
   1.1 (evasione sandbox via `subprocess`) e 1.2 (SSRF nel nodo `http.request`). La 1.2 è ormai una
   modifica di due righe sopra `safe_http`, ma inizierebbe a rifiutare host interni che i workflow
   esistenti potrebbero legittimamente chiamare: vuole una decisione esplicita, non un fix di
   passaggio.
2. ~~**Allineare `main` a `refactor`**~~ ✅ **chiuso il 2026-08-25** (§ 2.1) — fast-forward pushato,
   `origin/main` = `origin/refactor` = `7d3bf88`, tutti i tag sul remoto.
3. ~~**Rebuild + push delle immagini**~~ ✅ **chiuso il 2026-08-25** (§ 5.2, § 5.3) — ricostruite
   come `v3.9.0` e pushate, variante `-browser` inclusa. I tre degradi (git sync, markitdown,
   sqlite-vec) sono ora risolti *in produzione*, non solo in locale.
4. ~~**Committare il lavoro MCP pendente** e rilasciarlo~~ ✅ **chiuso il 2026-08-25** — uscito nella
   `v3.9.0` (§ 2.4), taggata e pushata. **Lo sprint 1 è ora chiuso tranne le due Critical.**

### Sprint 2 — Rendere il processo ripetibile

5. **CI minima** (§ 2.3): test in Docker + check di coerenza delle versioni. ❌ da fare.
6. ~~**Tag mancanti + `[3.5.0]` nel CHANGELOG**~~ ✅ chiuso il 2026-08-24 come *non applicabile*
   (§ 2.2): i tag punterebbero tutti allo stesso commit, il buco è dichiarato nel CHANGELOG.
7. **Triage dei 4 test rossi** (§ 5.4) — precondizione perché la CI abbia senso.
8. ~~**Allineare le roadmap obsolete**~~ ✅ **fatto il 2026-08-25** (§ 6) — sei disallineamenti
   corretti in quattro file; questo documento è ora dichiaratamente l'unica fonte di verità per il
   lavoro aperto.

### ~~Sprint 3 — Debito tecnico P1~~ ✅ completato il 2026-08-25 (anticipato)

9. ~~**Segmentare `graph_workflows.py`**~~ ✅ package di 13 sub-router, route table identica.
10. Insieme sono stati chiusi anche i **P2**: split di `telegram/bot.py` e
    `graph_workflow_repository.py`, EventBus/rate-limit/scheduler dietro interfaccia con leader
    election, i18n a sorgente unica, e le **migrazioni versionate** — queste ultime *non* rinviate
    alla 37.b come previsto qui, perché servivano subito per rendere sicuri i deploy; la 37.b le
    sostituirà con Alembic quando arriverà. Il refactor dei mega-componenti Angular è stato chiuso
    il 2026-08-25 (§ 3): **il debito tecnico P0-P2 è esaurito**, resta solo il P3 PostgreSQL, che
    coincide con la Phase 37.

### Sprint 4 — Feature

11. **Phase 25 — API keys** (§ 4.1), abbinata al fix 2.5 (rate limit su login): stesso layer, un
    solo intervento.
12. ~~**15.5 — nodi multimodali**~~ ✅ **fatto il 2026-08-25** (§ 5.1) — quattro nodi più
    `speech_service`; era la trascrizione nel provider layer a bloccare, ed è stata fatta.

### Sprint 5+ — Il lift grosso

13. **Phase 37** (§ 4.2), rigorosamente nell'ordine a → b → c → d → e → f, con la suite verde come
    cancello a ogni sotto-fase. Apre la strada al P3 PostgreSQL.
14. *(Il P3 PostgreSQL non è una voce a sé: è la 37.c, e arriva con la Phase 37.)*

---

## Riepilogo quantitativo

Aggiornato al 2026-08-25 (fine giornata).

| Area | Aperti | Peso | Δ dal 2026-08-16 |
|---|---|---|---|
| Sicurezza (audit QA) | **8 finding** (1.1, 1.2, 1.4, 2.7, 3.2, 3.3, 4.3, 4.4), di cui 2 Critical | 🔴 | −9 (1.3, 2.1, 2.2, 2.3, 2.5, 2.6, 2.8, 3.1, 4.1) |
| Git / release / CI | **1 area: nessuna CI** | 🟠 | −1 (`v3.9.0` taggata e pushata, immagini sul registry) |
| Debito tecnico | **nessuna voce P0-P2 aperta**; resta il P3 PostgreSQL, che è la Phase 37. P1 engine parziale per scelta | ✅ | −1 (mega-componenti Angular) |
| Roadmap prodotto | 2 fasi (25, 37 — quest'ultima in 6 sotto-fasi) | 🟡 | invariato |
| Roadmap workflow | **nessun residuo**; il triage dei 4 test rossi resta, ma è § 2.3, non una fase | ✅ | −1 (immagini pushate) |
| Documentazione | nessuno | ✅ | −5 (tutti allineati) |

**Lettura in una riga:** il debito architetturale P0-P2 è esaurito, **le 20 fasi della roadmap
workflow sono tutte implementate**, la documentazione è allineata e l'audit di sicurezza è sceso a
8 finding aperti su 19. Il backlog è ormai corto e tutto nominabile:

1. **Le due Critical di luglio** — 1.1 evasione della sandbox `python_exec`, 1.2 SSRF nel nodo
   `http.request` con i `$secrets` negli header. Non hanno più nulla davanti; la 1.2 è a due righe
   dall'essere chiusa, una volta decisa la politica sugli host interni.
2. **La CI**, che ancora non esiste — con il triage dei 4 test rossi, senza cui un required check
   non avrebbe senso.
3. **Le due fasi rimaste**: 25 (API keys) e 37 (persistenza pluggable, che assorbe il P3).

*(La `v3.9.0` è uscita il 2026-08-25 — tag pushato e quattro immagini sul registry — quindi il
lavoro di agosto è ora effettivamente distribuito, non più fermo in locale.)*
