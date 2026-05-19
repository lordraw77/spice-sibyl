DOCKER_USER   ?= lordraw77
BACKEND_IMAGE  = $(DOCKER_USER)/spice-sibyl-backend
FRONTEND_IMAGE = $(DOCKER_USER)/spice-sibyl-frontend
VERSION       ?= latest

.PHONY: up down logs backend frontend test-backend install-backend install-frontend \
        build push release prod-up prod-down

# ── Development ───────────────────────────────────────────────────────────────
up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

backend:
	cd backend && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm install && npm start

test-backend:
	cd backend && pytest

install-backend:
	cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

install-frontend:
	cd frontend && npm install

# ── Production ────────────────────────────────────────────────────────────────
prod-up:
	docker compose -f docker-compose.prod.yml up -d

prod-down:
	docker compose -f docker-compose.prod.yml down

# ── Docker Hub ────────────────────────────────────────────────────────────────
build:
	docker build -t $(BACKEND_IMAGE):$(VERSION) ./backend
	docker build -f ./frontend/Dockerfile.prod -t $(FRONTEND_IMAGE):$(VERSION) ./frontend

push:
	docker push $(BACKEND_IMAGE):$(VERSION)
	docker push $(FRONTEND_IMAGE):$(VERSION)

# Build, tag as latest + version, and push  — usage: make release VERSION=v1.2.3
release: build
	docker tag $(BACKEND_IMAGE):$(VERSION)  $(BACKEND_IMAGE):latest
	docker tag $(FRONTEND_IMAGE):$(VERSION) $(FRONTEND_IMAGE):latest
	docker push $(BACKEND_IMAGE):$(VERSION)
	docker push $(BACKEND_IMAGE):latest
	docker push $(FRONTEND_IMAGE):$(VERSION)
	docker push $(FRONTEND_IMAGE):latest
