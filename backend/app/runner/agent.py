"""
Phase 46 — roadmap fase 14.1: remote runner agent.

A lightweight, **outbound-only** process meant to run on another machine
(private network, DMZ, a GPU box) — same repo/image as the backend, started
with a per-runner token instead of a user session:

    SIBYL_API_URL=https://backend.example.com/api/v1 \\
    SIBYL_RUNNER_TOKEN=<token from POST /graph-workflows/runners> \\
    python -m app.runner.agent

It never opens an inbound port and never touches the backend's database
directly — only its HTTP API, authenticated by ``X-Runner-Token``:

  * heartbeats periodically (``POST /wf/runners/heartbeat``) so the Runners
    page shows it online, with its version and (optionally refreshed) labels;
  * long-polls for the next assigned job (``GET /wf/runners/jobs/next``) —
    the fase 3.1 ``test_node()`` contract: ``{node_id, node_type, params
    (already expression-resolved — any ``$secrets`` are inlined literal
    values, never the vault), input}``;
  * executes it locally via :func:`app.services.workflow_graph_service.
    _dispatch_stateless` — the same subset of node executors the backend
    itself falls back to, needing no database/profile context — and posts
    the result back (``POST /wf/runners/jobs/{id}/result``) as ``{ok, output,
    handles, logs}``.

The dispatcher only ever routes ``_REMOTE_CAPABLE_TYPES`` here (http.request,
code, db.query, set, if, switch, merge, filter, aggregate, batch, wait); the
backend enforces each runner's own node-type allow-list on top of that when
picking a candidate, so e.g. a DMZ runner can be limited to ``http.request``
only even though this agent could technically run any of them.
"""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger("sibyl_runner")

_API_URL = os.environ.get("SIBYL_API_URL", "http://localhost:8000/api/v1").rstrip("/")
_TOKEN = os.environ.get("SIBYL_RUNNER_TOKEN", "")
_HEARTBEAT_SECONDS = float(os.environ.get("SIBYL_RUNNER_HEARTBEAT_SECONDS", "30"))
_POLL_WAIT_SECONDS = float(os.environ.get("SIBYL_RUNNER_POLL_WAIT_SECONDS", "20"))
_VERSION = os.environ.get("SIBYL_RUNNER_VERSION", "1.0")


async def _heartbeat_loop(client: httpx.AsyncClient) -> None:
    while True:
        try:
            resp = await client.post("/wf/runners/heartbeat", json={"version": _VERSION})
            resp.raise_for_status()
        except Exception:  # noqa: BLE001 — one failed heartbeat must not kill the agent
            logger.warning("heartbeat failed", exc_info=True)
        await asyncio.sleep(_HEARTBEAT_SECONDS)


async def _run_job(job: dict) -> dict:
    from app.services.workflow_graph_service import _dispatch_stateless

    try:
        output, handles = await _dispatch_stateless(
            job["node_type"], job.get("params") or {}, job.get("input")
        )
        return {"ok": True, "output": output, "handles": handles, "logs": []}
    except Exception as exc:  # noqa: BLE001 — report the failure, never crash the agent
        logger.warning("job %s failed: %s", job.get("job_id"), exc)
        return {"ok": False, "output": None, "handles": [], "error": str(exc), "logs": []}


async def _poll_loop(client: httpx.AsyncClient) -> None:
    while True:
        try:
            resp = await client.get("/wf/runners/jobs/next", params={"wait": _POLL_WAIT_SECONDS})
            if resp.status_code == 204 or not resp.content:
                continue
            resp.raise_for_status()
            job = resp.json()
            if not job:
                continue
            logger.info("executing job %s (%s)", job.get("job_id"), job.get("node_type"))
            result = await _run_job(job)
            await client.post(f"/wf/runners/jobs/{job['job_id']}/result", json=result)
        except httpx.HTTPError:
            logger.warning("poll/execute cycle failed", exc_info=True)
            await asyncio.sleep(2)
        except Exception:  # noqa: BLE001 — the poll loop must never die
            logger.exception("unexpected poll loop error")
            await asyncio.sleep(2)


async def main() -> None:
    if not _TOKEN:
        raise SystemExit("SIBYL_RUNNER_TOKEN is required (see POST /graph-workflows/runners)")
    logging.basicConfig(level=logging.INFO)
    headers = {"X-Runner-Token": _TOKEN}
    timeout = httpx.Timeout(_POLL_WAIT_SECONDS + 15)
    async with httpx.AsyncClient(base_url=_API_URL, headers=headers, timeout=timeout) as client:
        logger.info("sibyl-runner agent starting against %s", _API_URL)
        await asyncio.gather(_heartbeat_loop(client), _poll_loop(client))


if __name__ == "__main__":
    asyncio.run(main())
