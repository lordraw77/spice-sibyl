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

## 2. Deploy su un server

### Preparazione (una-tantum)

```bash
# Copia i file minimi sul server
scp backend/.env.example user@server:~/spice-sibyl/backend/.env
scp docker-compose.prod.yml user@server:~/spice-sibyl/
scp -r shared-config user@server:~/spice-sibyl/
```

### Configurazione `.env`

Modifica `backend/.env` con i valori reali:

```env
# Obbligatori
API_KEY=una-stringa-segreta-lunga
VAULT_SECRET_KEY=un-altra-stringa-segreta-lunga
CORS_ORIGINS=https://tuodominio.com

# Almeno un provider
GROQ_API_KEY=gsk_...

# Telegram (opzionale)
TELEGRAM_BOT_TOKEN=1234567890:AAF...
TELEGRAM_DEFAULT_MODEL=groq/llama-3.3-70b-versatile
```

> **`VAULT_SECRET_KEY`** cifra le chiavi API salvate tramite UI. Se lo cambi, le chiavi vaultate esistenti diventano illeggibili — imposta un valore stabile e conservalo.

### Avvio

```bash
cd ~/spice-sibyl
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Servizi in ascolto:

| Servizio | Porta | URL |
|---|---|---|
| Frontend (nginx) | 80 | `http://server` |
| Backend (FastAPI) | 8000 | `http://server:8000/api/v1` |

### Variabile `FRONTEND_API_URL`

Il frontend deve sapere dove raggiungere il backend **dal browser** (non dall'interno di Docker).

```bash
# Esempio con IP pubblico o dominio
FRONTEND_API_URL=http://tuodominio.com:8000/api/v1 \
  docker compose -f docker-compose.prod.yml up -d
```

Oppure nel file `.env` della root del progetto:

```env
FRONTEND_API_URL=http://tuodominio.com:8000/api/v1
```

---

## 3. Aggiornamento

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Il volume `spice-sibyl-db` persiste il database SQLite (conversazioni, profili, chiavi vault) tra gli aggiornamenti.

---

## 4. Sviluppo locale

```bash
cp backend/.env.example backend/.env
# edita backend/.env con APP_ENV=development e le chiavi che vuoi usare

make up          # docker compose up --build  (hot-reload backend + ng serve frontend)
make down        # ferma tutto
make logs        # segui i log
```

Oppure senza Docker:

```bash
make install-backend   # crea venv + pip install
make install-frontend  # npm install

make backend    # uvicorn :8000 con --reload
make frontend   # ng serve :4200
```

---

## 5. Struttura immagini

### Backend (`lordraw/spice-sibyl-backend`)

- Base: `python:3.12-slim`
- Utente non-root (`app`)
- Volume `/data` → SQLite DB (impostare `DB_PATH=/data/spice_sibyl.db`)
- Healthcheck su `GET /api/v1/health`
- Porta `8000`

### Frontend (`lordraw/spice-sibyl-frontend`)

- Build: `node:20-alpine` → `ng build --configuration production`
- Serve: `nginx:alpine`
- `API_URL` iniettata via `envsubst` all'avvio del container in `config/app-config.json`
- Porta `80`

---

## 6. Reverse proxy (opzionale)

Per esporre tutto su HTTPS con un unico dominio, esempio con **nginx** o **Caddy** sul server host:

### Caddy (`Caddyfile`)

```
tuodominio.com {
    reverse_proxy /api/* localhost:8000
    reverse_proxy localhost:80
}
```

### nginx (`/etc/nginx/sites-available/spice-sibyl`)

```nginx
server {
    listen 443 ssl;
    server_name tuodominio.com;

    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
    }

    location / {
        proxy_pass http://localhost:80;
        proxy_set_header Host $host;
    }
}
```

Con reverse proxy puoi impostare `FRONTEND_API_URL=https://tuodominio.com/api/v1` e non esporre la porta `8000` all'esterno.
