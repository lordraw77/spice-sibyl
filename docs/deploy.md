# SpiceSibyl — Build & Deploy

---

## Prerequisiti

- Docker + Docker Compose v2
- Account Docker Hub (`lordraw77`)
- Repository GitHub con i secret configurati (solo per CI/CD automatico)

---

## 1. Pubblicare su Docker Hub

### Automatico (raccomandato) — GitHub Actions

Il workflow `.github/workflows/docker-publish.yml` si attiva su ogni tag `v*.*.*` e pubblica entrambe le immagini per `linux/amd64` e `linux/arm64`.

**Setup una-tantum — secret GitHub:**

```
Repository → Settings → Secrets and variables → Actions → New repository secret

DOCKERHUB_USERNAME   lordraw77
DOCKERHUB_TOKEN      <token generato su hub.docker.com → Account Settings → Security>
```

**Rilascio:**

```bash
git tag v1.2.3
git push origin v1.2.3
```

Le immagini prodotte:

```
lordraw/spice-sibyl-backend:v1.2.3
lordraw/spice-sibyl-backend:latest
lordraw/spice-sibyl-frontend:v1.2.3
lordraw/spice-sibyl-frontend:latest
```

---

### Manuale — da locale

```bash
docker login

# Build + tag + push in un comando
make release VERSION=v1.2.3

# Oppure step separati
make build   VERSION=v1.2.3   # solo build locale
make push    VERSION=v1.2.3   # solo push
```

---

## 2. Architettura produzione

In produzione un singolo container **nginx** serve sia il frontend Angular (file statici) sia fa da reverse proxy verso il backend. Frontend e backend non sono esposti su porte separate.

```
                 ┌──────────────────────────┐
  :80 / :443 ──►│        nginx              │
                 │  /        → Angular SPA   │
                 │  /api/*   → backend:8000  │
                 └──────────┬───────────────┘
                            │
                     ┌──────▼──────┐
                     │   backend   │
                     │   :8000     │
                     └─────────────┘
```

---

## 3. Deploy su un server

### Preparazione (una-tantum)

```bash
# Clona o copia il progetto sul server
git clone <repo> ~/spice-sibyl
cd ~/spice-sibyl

cp backend/.env.example backend/.env
# Edita backend/.env con i valori reali (vedi sotto)
```

### Configurazione `backend/.env`

```env
# Obbligatori
APP_ENV=production
API_KEY=una-stringa-segreta-lunga
VAULT_SECRET_KEY=un-altra-stringa-segreta-lunga

# Autenticazione (Fase 13) — OBBLIGATORIA su tutte le API.
JWT_SECRET_KEY=una-stringa-segreta-molto-lunga
# Admin di bootstrap creato al primo avvio se la tabella utenti è vuota.
# Senza queste due variabili su un DB nuovo NESSUNO può accedere.
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=una-password-forte
RATE_LIMIT_DEFAULT=60/minute

# PUBLIC_URL — dominio pubblico DDNS / reverse proxy.
# Aggiunto automaticamente ai CORS origins.
PUBLIC_URL=https://sibyl.example.com

# Almeno un provider
GROQ_API_KEY=gsk_...

# Telegram (opzionale)
TELEGRAM_BOT_TOKEN=1234567890:AAF...
TELEGRAM_ALLOWED_USERS=18278029
TELEGRAM_DEFAULT_MODEL=groq/llama-3.3-70b-versatile

# Multi-MCP orchestrator (opzionale)
ORCHESTRATOR_BASE_URL=http://host.docker.internal:8910/v1
```

> **`VAULT_SECRET_KEY`** cifra le chiavi API salvate tramite UI. Se lo cambi, le chiavi vaultate esistenti diventano illeggibili — imposta un valore stabile e conservalo.

> **`PUBLIC_URL`** viene aggiunto automaticamente alla lista `CORS_ORIGINS` del backend, così sia `localhost` che l'accesso esterno funzionano senza duplicare configurazioni.

### Avvio

```bash
cd ~/spice-sibyl
docker compose -f docker-compose.prod.yml up -d --build
```

Servizi in ascolto:

| Servizio | Porta | URL |
|---|---|---|
| Nginx (frontend + proxy) | 80, 443 | `http://server` o `https://server` |
| Backend (interno) | — | non esposto, raggiunto via nginx |

> Il frontend usa URL API relative (`/api/v1`) — non serve più configurare `FRONTEND_API_URL`. Funziona automaticamente con qualsiasi dominio/IP.

---

## 4. HTTPS / TLS

### Opzione A: Certificati manuali

Copia `fullchain.pem` e `privkey.pem` nella cartella `nginx/ssl/`:

```bash
cp /path/to/fullchain.pem nginx/ssl/
cp /path/to/privkey.pem   nginx/ssl/
docker compose -f docker-compose.prod.yml restart nginx
```

L'entrypoint rileva automaticamente i certificati e attiva il server HTTPS.

### Opzione B: Let's Encrypt (Certbot)

1. Decommenta il servizio `certbot` in `docker-compose.prod.yml`
2. Esegui il primo rilascio manuale:

```bash
docker compose -f docker-compose.prod.yml up -d nginx

docker run --rm \
  -v ./nginx/ssl:/etc/letsencrypt \
  -v certbot-www:/var/www/certbot \
  certbot/certbot certonly \
    --webroot -w /var/www/certbot \
    -d sibyl.example.com \
    --agree-tos -m tuaemail@example.com

docker compose -f docker-compose.prod.yml restart nginx
```

3. Il sidecar `certbot` rinnova automaticamente ogni 12h.

### Opzione C: Solo HTTP

Se non monti certificati in `nginx/ssl/`, nginx serve solo su porta 80 senza redirect. Utile per reti locali o se termini TLS altrove (Cloudflare, Caddy, ecc.).

---

## 5. DDNS / Accesso remoto

Per esporre SpiceSibyl su Internet con un dominio dinamico:

1. Configura il servizio DDNS sul tuo router (DuckDNS, No-IP, Dynu, ecc.)
2. Apri le porte **80** e **443** (port forwarding) verso il server
3. Imposta `PUBLIC_URL` in `backend/.env`:

```env
PUBLIC_URL=https://sibyl.duckdns.org
```

4. (Opzionale) Configura TLS con Let's Encrypt (vedi sopra)
5. Riavvia:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

---

## 6. Aggiornamento

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --build
```

Il volume `/opt/data` persiste il database SQLite (conversazioni, profili, chiavi vault) tra gli aggiornamenti.

---

## 7. Sviluppo locale

```bash
cp backend/.env.example backend/.env
# edita backend/.env con APP_ENV=development e le chiavi che vuoi usare

make up          # docker compose up --build  (hot-reload backend + ng serve frontend)
make down        # ferma tutto
make logs        # segui i log
```

In dev, frontend e backend girano su porte separate (4200 e 8000). Il file `app-config.json` punta a `http://localhost:8000/api/v1`.

Oppure senza Docker:

```bash
make install-backend   # crea venv + pip install
make install-frontend  # npm install

make backend    # uvicorn :8000 con --reload
make frontend   # ng serve :4200
```

---

## 8. Struttura immagini

### Backend (`lordraw/spice-sibyl-backend`)

- Base: `python:3.12-slim`
- Utente non-root (`app`)
- Volume `/data` → SQLite DB (impostare `DB_PATH=/data/spice_sibyl.db`)
- Healthcheck su `GET /api/v1/health`
- Porta `8000` (interna)

### Nginx (`spice-sibyl-nginx`)

- Multi-stage: `node:20-alpine` (build Angular) → `nginx:1.27-alpine`
- Serve il frontend Angular su `/` e proxy `/api/*` al backend
- `API_URL` iniettata via `envsubst` all'avvio in `config/app-config.json`
- TLS abilitato automaticamente se `nginx/ssl/fullchain.pem` + `privkey.pem` presenti
- Porte `80` e `443`

---

## 9. Variabili d'ambiente

| Variabile | Dove | Descrizione |
|---|---|---|
| `PUBLIC_URL` | `backend/.env` | URL pubblica (DDNS/dominio); aggiunta ai CORS origins |
| `VAULT_SECRET_KEY` | `backend/.env` | Chiave master per cifratura API keys nel vault |
| `API_KEY` | `backend/.env` | Bearer token per autenticare le richieste API |
| `JWT_SECRET_KEY` | `backend/.env` | Segreto firma JWT access/refresh (Fase 13) — **cambialo** |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `backend/.env` | Admin di bootstrap creato al primo avvio (richiesti su DB nuovo) |
| `JWT_ACCESS_TTL_MINUTES` / `JWT_REFRESH_TTL_DAYS` | `backend/.env` | Durata token (default 30 min / 14 giorni) |
| `RATE_LIMIT_DEFAULT` | `backend/.env` | Rate limit per-utente (default `60/minute`) |
| `CORS_ORIGINS` | `backend/.env` | Lista origini CORS aggiuntive (comma-separated) |
| `API_URL` | `nginx` env | URL API iniettata nel frontend (default: `/api/v1`) |
| `EMBEDDING_CHAIN` | `backend/.env` | RAG embedding provider fallback chain (`provider:model,...`); default `ollama:nomic-embed-text,gemini:text-embedding-004,mistral:mistral-embed` |
| `TIMEZONE` | `backend/.env` | IANA timezone for Telegram reminders (default `Europe/Rome`) |

> **Phase 13 (auth)** adds Python dependencies (`PyJWT`, `bcrypt`, `email-validator`) and makes authentication **mandatory**. After pulling, **rebuild** the backend and set `JWT_SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` in `backend/.env` before restarting — otherwise the first boot logs a SECURITY warning and no one can log in. Existing pre-auth profiles are automatically adopted by the bootstrap admin.
>
> **Phase 14 (RAG + reminders)** adds Python dependencies (`numpy`, `python-multipart`, `python-telegram-bot[job-queue]`). After pulling these changes you must **rebuild** the backend image so they are installed:
>
> ```bash
> docker compose up -d --build backend
> ```
>
> RAG embeddings need at least one reachable embedding provider — local Ollama is the zero-cost default (`ollama pull nomic-embed-text`). Telegram reminders use the `JobQueue` (APScheduler) provided by the `[job-queue]` extra; without it `/remind` reports that the scheduler is unavailable.

---

## 10. Multi-MCP orchestrator sidecar (opzionale, agent mode)

Per abilitare il modello `agent/multi-mcp` (orchestratore multi-agente), deploya il **sidecar orchestrator** dal progetto `multi-mcp` e punta il backend ad esso.

1. **Avvia il sidecar** (compatibile OpenAI, porta `8910`):

   ```bash
   cd /opt/multi-mcp
   cp .env.example .env && $EDITOR .env
   make docker-up
   curl http://localhost:8910/health
   ```

2. **Collega il backend** — imposta `ORCHESTRATOR_BASE_URL` in `backend/.env`:

   ```env
   ORCHESTRATOR_BASE_URL=http://<orchestrator-host>:8910/v1
   ```

3. **Registra il modello** — assicurati che il catalogo (`provider_models.yaml` in `/config`) contenga un blocco `agent` con `agent/multi-mcp`, poi `docker compose restart backend`.

Seleziona **agent/multi-mcp** nel picker modelli web, o usa `/agent` in Telegram.
