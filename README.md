# spice-sibyl
One gateway, many minds

## Stack
- Backend: FastAPI, LiteLLM, SSE
- Frontend: Angular, Bootstrap, PrimeNG-ready
- Dev environment: Docker Compose, Makefile

## Struttura
- `backend/`: API OpenAI-compatible e adapter provider
- `frontend/`: web console
- `docs/`: Documents 

## Start

### Docker
```bash
docker compose up --build
```

### local 
```bash
make install-backend
make install-frontend
make backend
make frontend
```

## Endpoint 
- `GET /api/v1/health`
- `GET /api/v1/models`
- `POST /api/v1/chat/completions`

## Provider 
- Mock
- Groq
- OpenRouter
- Ollama
