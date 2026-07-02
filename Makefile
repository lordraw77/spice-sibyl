DOCKER_USER   ?= lordraw
BACKEND_IMAGE  = $(DOCKER_USER)/spice-sibyl-backend
FRONTEND_IMAGE = $(DOCKER_USER)/spice-sibyl-frontend
NGINX_IMAGE    = $(DOCKER_USER)/spice-sibyl-nginx
GIT_TAG       := $(shell git describe --tags --abbrev=0 2>/dev/null || echo "latest")
VERSION       ?= $(GIT_TAG)

.PHONY: up down logs backend frontend test-backend install-backend install-frontend \
        build push release prod-up prod-down \
        dev dev-build dev-build-backend dev-build-frontend rebuild publish

# ── Development ───────────────────────────────────────────────────────────────
# Build EVERYTHING for dev: the backend image (code is baked into the image, so a
# rebuild is required to pick up changes) and the nginx image, which compiles the
# Angular frontend from source and bundles it. The nginx image is tagged with the
# exact name docker-compose.yml expects, so `up` then uses the freshly built one
# instead of pulling the published `latest`.
dev-build: dev-build-frontend dev-build-backend

dev-build-backend:
	docker compose build backend

dev-build-frontend:
	docker build -f ./nginx/Dockerfile -t $(NGINX_IMAGE):latest .

# One-shot dev workflow: rebuild all images, (re)start the stack detached, tail logs.
dev: dev-build
	docker compose up -d
	docker compose logs -f

# Rebuild all images and bring the stack back up in the foreground.
rebuild: dev-build
	docker compose up --force-recreate

# Start the stack. NOTE: this only rebuilds the backend (nginx uses a prebuilt
# image) — run `make dev-build` / `make dev` to also pick up frontend changes.
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
	docker build -f ./nginx/Dockerfile -t $(NGINX_IMAGE):$(VERSION) .

push:
	docker push $(BACKEND_IMAGE):$(VERSION)
	docker push $(FRONTEND_IMAGE):$(VERSION)
	docker push $(NGINX_IMAGE):$(VERSION)
	docker tag $(BACKEND_IMAGE):$(VERSION)  $(BACKEND_IMAGE):latest
	docker tag $(FRONTEND_IMAGE):$(VERSION) $(FRONTEND_IMAGE):latest
	docker tag $(NGINX_IMAGE):$(VERSION)    $(NGINX_IMAGE):latest
	docker push $(BACKEND_IMAGE):latest
	docker push $(FRONTEND_IMAGE):latest
	docker push $(NGINX_IMAGE):latest


# Build, tag as latest + version, and push  — usage: make release VERSION=v1.2.3
release: build
	docker tag $(BACKEND_IMAGE):$(VERSION)  $(BACKEND_IMAGE):latest
	docker tag $(FRONTEND_IMAGE):$(VERSION) $(FRONTEND_IMAGE):latest
	docker tag $(NGINX_IMAGE):$(VERSION)    $(NGINX_IMAGE):latest
	docker push $(BACKEND_IMAGE):$(VERSION)
	docker push $(BACKEND_IMAGE):latest
	docker push $(FRONTEND_IMAGE):$(VERSION)
	docker push $(FRONTEND_IMAGE):latest
	docker push $(NGINX_IMAGE):$(VERSION)
	docker push $(NGINX_IMAGE):latest

publish: build push
