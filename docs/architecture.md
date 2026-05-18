# SpiceSibyl Architecture

## Goals
- Gateway unico verso più provider AI
- API OpenAI-compatible su `/v1/chat/completions`
- UI web stile chat moderna con storico e configurazione provider
- Supporto streaming SSE e routing provider/modello

## Monorepo layout
- `backend/app/api/v1/endpoints`: endpoint REST
- `backend/app/providers`: adapter provider
- `backend/app/services`: logica applicativa
- `frontend/src/app/features`: feature Angular
- `frontend/src/app/core`: servizi condivisi e modelli
- `frontend/src/app/layout`: shell applicativa

## MVP
1. Healthcheck + metadata API
2. `/v1/models`
3. `/v1/chat/completions` mock OpenAI-compatible
4. Chat UI Angular con model selector
